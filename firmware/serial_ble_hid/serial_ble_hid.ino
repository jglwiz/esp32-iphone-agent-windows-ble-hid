#include <Arduino.h>
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>
#include <esp_system.h>
#include <esp_mac.h>
#include <atomic>
#include <math.h>
#include "cJSON.h"
#include "hid_reports.h"
#include "services/gatt/ble_svc_gatt.h"


static const char *VERSION = "0.3.3";
static const char *NAME = "S3-iPhone-HID";
static const uint32_t LEASE_MS = 2000, REPORT_MS = 20;
NimBLEServer *server;
NimBLEHIDDevice *hid;
NimBLECharacteristic *keyInput, *mouseInput, *absInput;
std::atomic<bool> absSubscribed{false};
uint16_t absX = 16384, absY = 16384;
uint8_t absButtons = 0;
String lastAbsHex;
uint32_t absReports = 0;
std::atomic<bool> keySubscribed{false}, mouseSubscribed{false}, bonded{false};
std::atomic<bool> connected{false}, encrypted{false}, disconnected{false};
std::atomic<int> authReason{-1}, notifyStatus{-1};
std::atomic<unsigned> ledState{0}, connInterval{0}, connectionGeneration{0};
String stableDeviceId;
std::atomic<bool> suspended{false};
String lastKeyHex, lastMouseHex;
String bootId, session;
uint32_t heartbeat = 0;
int highestId = 0;
uint8_t keys[8] = {}, buttons = 0;
bool keyNeutral = false, mouseNeutral = false;
bool lastKeyReady = false, lastMouseReady = false;

struct ReplyCache { int id = 0; String request, response; } cache[16];
unsigned cacheNext = 0;
struct Action {
  bool active = false;
  int id = 0, dx = 0, dy = 0, wheel = 0, phase = 0;
  int x = 0, y = 0, startX = 0, startY = 0, frames = 1;
  String op, request;
  uint8_t usage = 0, modifiers = 0, button = 1;
  uint32_t next = 0;
} action;

String encode(cJSON *json) {
  char *p = cJSON_PrintUnformatted(json);
  String result = p ? p : "{}";
  cJSON_free(p); cJSON_Delete(json);
  return result;
}
cJSON *envelope(int id, const char *state) {
  cJSON *j = cJSON_CreateObject();
  cJSON_AddNumberToObject(j,"v",1);
  cJSON_AddStringToObject(j,"boot_id",bootId.c_str());
  cJSON_AddStringToObject(j,"session",session.c_str());
  cJSON_AddNumberToObject(j,"id",id);
  cJSON_AddStringToObject(j,"state",state);
  return j;
}
void emitEvent(const char *name) {
  cJSON *j = envelope(0,"event");
  cJSON_AddStringToObject(j,"event",name);
  Serial0.println(encode(j));
}
void reply(int id, const char *state, const char *error = nullptr, const String &request = "") {
  cJSON *j = envelope(id,state);
  if (error) cJSON_AddStringToObject(j,"error",error);
  String out = encode(j);
  if (request.length()) {
    auto &entry = cache[cacheNext++ % 16];
    entry.id = id; entry.request = request; entry.response = out;
  }
  Serial0.println(out);
}
bool ready(bool keyboard) {
  return connected && encrypted && !suspended && (keyboard ? keySubscribed.load() : mouseSubscribed.load());
}
String hexBytes(const uint8_t *data, size_t size) {
  String out;
  for (size_t i=0; i<size; i++) { char h[3]; snprintf(h,sizeof(h),"%02x",data[i]); out += h; }
  return out;
}
bool sendReport(bool keyboard, uint8_t *data, size_t size) {
  if (!ready(keyboard)) return false;
  auto *ch = keyboard ? keyInput : mouseInput;
  notifyStatus = -1;
  ch->setValue(data,size);
  auto stored = ch->getValue();
  if (stored.length() != size || memcmp(stored.c_str(),data,size)) return false;
  if (keyboard) lastKeyHex = hexBytes(data,size); else lastMouseHex = hexBytes(data,size);
  return ch->notify();
}
bool sendKeys() { return sendReport(true, keys, sizeof(keys)); }
bool sendMouse(int dx = 0, int dy = 0, int wheel = 0) {
  uint8_t data[] = {buttons,(uint8_t)(int8_t)dx,(uint8_t)(int8_t)dy,(uint8_t)(int8_t)wheel};
  return sendReport(false,data,sizeof(data));
}
bool absReady() {
  return connected && encrypted && !suspended && absSubscribed;
}
bool sendAbsolute(uint16_t x, uint16_t y, uint8_t held) {
  if (!absReady()) return false;
  uint8_t data[] = {held, (uint8_t)x, (uint8_t)(x >> 8), (uint8_t)y, (uint8_t)(y >> 8)};
  absInput->setValue(data, sizeof(data));
  // These are last commanded coordinates, never a position read back from iOS.
  absX = x; absY = y;
  // A failed press may still have reached the peer: retain release obligation.
  if (held) absButtons = held;
  lastAbsHex = hexBytes(data, sizeof(data));
  bool ok = absInput->notify();
  if (ok) { absReports++; absButtons = held; }
  return ok;
}
bool releaseAll() {
  // A session opening/closing must not inject an absolute move.
  // Only an outstanding absolute button press requires an absolute release.
  bool absOk = !absButtons || sendAbsolute(absX, absY, 0);
  memset(keys,0,sizeof(keys)); buttons = 0;
  keyNeutral = sendKeys(); mouseNeutral = sendMouse();
  return absOk && keyNeutral && mouseNeutral;
}
bool abortAction(const char *reason) {
  if (action.active) {
    reply(action.id,"error",reason,action.request);
    action.active = false;
  }
  return releaseAll();
}
void status(int id) {
  cJSON *j = envelope(id,"ok");
  cJSON_AddStringToObject(j,"firmware",VERSION);
  cJSON_AddStringToObject(j,"name",NAME);
  cJSON_AddStringToObject(j,"device_id",stableDeviceId.c_str());
  cJSON_AddNumberToObject(j,"connection_generation",connectionGeneration.load());
  cJSON_AddBoolToObject(j,"supports_absolute_pointer",true);
  cJSON_AddNumberToObject(j,"absolute_descriptor_version",1);
  cJSON_AddBoolToObject(j,"absolute_subscribed",absSubscribed);
  cJSON_AddBoolToObject(j,"absolute_ready",absReady() && !absButtons);
  cJSON_AddNumberToObject(j,"absolute_handle",absInput->getHandle());
  cJSON_AddNumberToObject(j,"last_commanded_x",absX);
  cJSON_AddNumberToObject(j,"last_commanded_y",absY);
  cJSON_AddNumberToObject(j,"absolute_buttons",absButtons);
  cJSON_AddNumberToObject(j,"absolute_reports",absReports);
  cJSON_AddStringToObject(j,"last_absolute_hex",lastAbsHex.c_str());
  cJSON_AddBoolToObject(j,"connected",connected);
  cJSON_AddBoolToObject(j,"encrypted",encrypted);
  cJSON_AddBoolToObject(j,"bonded",bonded);
  cJSON_AddNumberToObject(j,"auth_reason",authReason);
  cJSON_AddBoolToObject(j,"keyboard_subscribed",keySubscribed);
  cJSON_AddBoolToObject(j,"mouse_subscribed",mouseSubscribed);
  cJSON_AddBoolToObject(j,"keyboard_ready",ready(true) && keyNeutral);
  cJSON_AddBoolToObject(j,"mouse_ready",ready(false) && mouseNeutral);
  cJSON_AddBoolToObject(j,"suspended",suspended);
  cJSON_AddNumberToObject(j,"leds",ledState);
  cJSON_AddNumberToObject(j,"keyboard_handle",keyInput->getHandle());
  cJSON_AddNumberToObject(j,"mouse_handle",mouseInput->getHandle());
  auto mode = hid->getProtocolMode()->getValue();
  cJSON_AddNumberToObject(j,"protocol_mode",mode.length() ? (uint8_t)mode[0] : -1);
  cJSON_AddStringToObject(j,"last_keyboard_hex",lastKeyHex.c_str());
  cJSON_AddStringToObject(j,"last_mouse_hex",lastMouseHex.c_str());
  cJSON_AddNumberToObject(j,"connection_interval_ms",connInterval.load()*1.25);
  cJSON_AddNumberToObject(j,"active_id",action.active ? action.id : 0);
  cJSON_AddNumberToObject(j,"lease_ms",LEASE_MS);
  cJSON_AddNumberToObject(j,"uptime_ms",millis());
  Serial0.println(encode(j));
}
void diagnostics(int id) {
  cJSON *j = envelope(id,"ok");
  auto *map = hid->getHidService()->getCharacteristic(NimBLEUUID((uint16_t)0x2A4B));
  auto value = map->getValue();
  cJSON_AddStringToObject(j,"report_map_hex",hexBytes((const uint8_t *)value.c_str(),value.length()).c_str());
  auto *ad = absInput->getDescriptorByUUID(NimBLEUUID((uint16_t)0x2908));
  cJSON_AddStringToObject(j,"absolute_reference_hex",hexBytes(ad->getValue().data(),ad->getValue().size()).c_str());
  auto *kd = keyInput->getDescriptorByUUID(NimBLEUUID((uint16_t)0x2908));
  auto *md = mouseInput->getDescriptorByUUID(NimBLEUUID((uint16_t)0x2908));
  cJSON_AddStringToObject(j,"keyboard_reference_hex",hexBytes(kd->getValue().data(),kd->getValue().size()).c_str());
  cJSON_AddStringToObject(j,"mouse_reference_hex",hexBytes(md->getValue().data(),md->getValue().size()).c_str());
  Serial0.println(encode(j));
}

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *s, NimBLEConnInfo &info) override {
    connectionGeneration++; connected = true; encrypted = info.isEncrypted(); bonded = info.isBonded();
    authReason = -1; suspended = false; connInterval = info.getConnInterval();
    s->updateConnParams(info.getConnHandle(),12,24,0,400);
    NimBLEDevice::startSecurity(info.getConnHandle());
  }
  void onDisconnect(NimBLEServer *, NimBLEConnInfo &, int reason) override {
    connected = false; encrypted = false; bonded = false; suspended = false;
    keySubscribed = false; mouseSubscribed = false; absSubscribed = false; disconnected = true;
    authReason = reason;
  }
  void onAuthenticationComplete(NimBLEConnInfo &info) override {
    encrypted = info.isEncrypted(); bonded = info.isBonded(); authReason = encrypted ? 0 : -2;
    // Existing bonds may cache the old mouse-first characteristic order.
    // Standard Service Changed refreshes GATT without deleting pairing keys.
    if (encrypted) ble_svc_gatt_changed(0x0001, 0xffff);
  }
  void onConnParamsUpdate(NimBLEConnInfo &info) override { connInterval = info.getConnInterval(); }
};
class ReportCallbacks : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic *ch, NimBLEConnInfo &, uint16_t value) override {
    if (ch == absInput) absSubscribed = (value & 1) != 0;
    if (ch == keyInput) keySubscribed = (value & 1) != 0;
    if (ch == mouseInput) mouseSubscribed = (value & 1) != 0;
  }
};
class LedCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *ch, NimBLEConnInfo &) override {
    auto value = ch->getValue();
    if (value.length()) ledState = (uint8_t)value[0];
  }
};
class ControlCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *ch, NimBLEConnInfo &) override {
    auto value = ch->getValue();
    if (value.length()) suspended = value[0] == 0;
  }
};
bool integer(cJSON *j, const char *name, int lo, int hi, int &out, int fallback = 0, bool required = true) {
  cJSON *v = cJSON_GetObjectItemCaseSensitive(j,name);
  if (!v && !required) { out = fallback; return true; }
  if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) || floor(v->valuedouble) != v->valuedouble || v->valuedouble < lo || v->valuedouble > hi) return false;
  out = v->valueint; return true;
}
void handleLine(const String &line) {
  const char *end = nullptr;
  cJSON *j = cJSON_ParseWithOpts(line.c_str(),&end,true);
  if (!cJSON_IsObject(j)) { cJSON_Delete(j); reply(0,"error","invalid_json"); return; }
  int id = 0, version = 0;
  auto *s = cJSON_GetObjectItemCaseSensitive(j,"session");
  auto *o = cJSON_GetObjectItemCaseSensitive(j,"op");
  if (!integer(j,"id",1,2147483647,id) || !integer(j,"v",1,1,version) || !cJSON_IsString(s) || !cJSON_IsString(o) || strlen(s->valuestring) < 1 || strlen(s->valuestring) > 32) {
    cJSON_Delete(j); reply(id,"error","invalid_envelope"); return;
  }
  String op = o->valuestring, incomingSession = s->valuestring;
  if (op == "hello") {
    if (session != incomingSession) {
      abortAction("session_changed_partial_unknown");
      session = incomingSession; highestId = 0;
      for (auto &entry : cache) { entry.id = 0; entry.request = ""; entry.response = ""; }
    }
    heartbeat = millis(); status(id); cJSON_Delete(j); return;
  }
  if (session != incomingSession) { reply(id,"error","hello_required"); cJSON_Delete(j); return; }
  heartbeat = millis();
  if (op == "status" || op == "ping") { status(id); cJSON_Delete(j); return; }
  if (op == "diag") { diagnostics(id); cJSON_Delete(j); return; }
  // Canonical JSON avoids whitespace differences on duplicate requests.
  char *canonical = cJSON_PrintUnformatted(j);
  String request = canonical; cJSON_free(canonical);
  for (auto &entry : cache) if (entry.id == id) {
    if (entry.request == request) Serial0.println(entry.response);
    else reply(id,"error","id_conflict");
    cJSON_Delete(j); return;
  }
  if (action.active && action.id == id) {
    reply(id, action.request == request ? "accepted" : "error",action.request == request ? nullptr : "id_conflict");
    cJSON_Delete(j); return;
  }
  if (id <= highestId) { reply(id,"error","stale_id_no_replay"); cJSON_Delete(j); return; }
  highestId = id;
  if (op == "release_all" || op == "cancel") {
    bool released = abortAction("cancelled_partial_unknown");
    reply(id,released ? "ok" : "error",released ? nullptr : "release_unconfirmed",request); cJSON_Delete(j); return;
  }
  if (action.active) { reply(id,"error","busy",request); cJSON_Delete(j); return; }
  int a = 0, b = 0;
  Action candidate;
  candidate.id = id; candidate.op = op; candidate.request = request;
  bool valid = true, keyboard = op.startsWith("keyboard.");
  if (op == "abs.move" || op == "abs.click") {
    valid = integer(j,"x",0,32767,candidate.x) && integer(j,"y",0,32767,candidate.y);
    if (op == "abs.move") valid = valid && integer(j,"frames",1,16,candidate.frames,1,false);
    candidate.startX = absX; candidate.startY = absY;
  } else if (op == "mouse.move") {
    valid = integer(j,"dx",-2048,2048,candidate.dx) && integer(j,"dy",-2048,2048,candidate.dy);
  } else if (op == "mouse.scroll") {
    valid = integer(j,"wheel",-127,127,candidate.wheel);
  } else if (op == "mouse.click" || op == "mouse.down" || op == "mouse.up") {
    valid = integer(j,"button",1,3,a,1,false); if (valid) candidate.button = 1 << (a-1);
  } else if (op == "keyboard.tap" || op == "keyboard.down") {
    valid = integer(j,"usage",0,101,a) && (a == 0 || a >= 4) && integer(j,"modifiers",0,255,b,0,false);
    candidate.usage = a; candidate.modifiers = b;
  } else if (op != "keyboard.up") valid = false;
  cJSON_Delete(j);
  if (!valid) { reply(id,"error","invalid_operation_or_argument",request); return; }
  if (op.startsWith("abs.") ? (!absReady() || absButtons) : (!ready(keyboard) || !(keyboard ? keyNeutral : mouseNeutral))) { reply(id,"error","hid_not_ready",request); return; }
  candidate.active = true; candidate.next = millis(); action = candidate;
  reply(id,"accepted");
}

void advanceAction() {
  if (!action.active || (int32_t)(millis()-action.next) < 0) return;
  bool keyboard = action.op.startsWith("keyboard.");
  if (action.op.startsWith("abs.") ? !absReady() : !ready(keyboard)) { abortAction("link_lost_partial_unknown"); return; }
  bool ok = false, done = true;
  uint32_t nextDelay = 60;
  if (action.op == "abs.move") {
    int step = action.phase + 1;
    int x = action.startX + (action.x - action.startX) * step / action.frames;
    int y = action.startY + (action.y - action.startY) * step / action.frames;
    ok = sendAbsolute(x, y, 0);
    done = step == action.frames;
  } else if (action.op == "abs.click") {
    ok = sendAbsolute(action.x, action.y, action.phase == 1 ? 1 : 0);
    done = action.phase == 2; // position, press, release; each separated by 60 ms.
  } else if (action.op == "mouse.move") {
    int dx = constrain(action.dx,-8,8), dy = constrain(action.dy,-8,8);
    ok = sendMouse(dx,dy); action.dx -= dx; action.dy -= dy;
    done = !action.dx && !action.dy;
  } else if (action.op == "mouse.scroll") {
    ok = sendMouse(0,0,action.wheel);
  } else if (action.op == "mouse.click") {
    if (!action.phase) { buttons |= action.button; done = false; }
    else buttons &= ~action.button;
    ok = sendMouse();
  } else if (action.op == "mouse.down" || action.op == "mouse.up") {
    if (action.op == "mouse.down") buttons |= action.button;
    else buttons &= ~action.button;
    ok = sendMouse();
  } else {
    memset(keys,0,sizeof(keys));
    if (action.op != "keyboard.up" && action.modifiers && action.usage) {
      // iOS can ignore a modifier that first appears alongside the base key.
      // Establish it separately, press/release the key, then release modifiers.
      // A longer simultaneous key hold does not provide this transition.
      // Keep all four tap phases: modifier -> modifier+key -> modifier -> neutral.
      // See doc/keyboard-modifiers.md and the shift_a_staged.json regression fixture.
      if (action.phase < 3) keys[0] = action.modifiers;
      if (action.phase == 1) keys[2] = action.usage;
      done = action.op == "keyboard.down" ? action.phase == 1 : action.phase == 3;
      if (action.phase == 0) nextDelay = 200;
    } else {
      if (action.op != "keyboard.up" && !action.phase) { keys[0] = action.modifiers; keys[2] = action.usage; }
      done = action.op != "keyboard.tap" || action.phase;
    }
    ok = sendKeys();
  }
  if (!ok) { abortAction("notify_failed_partial_unknown"); return; }
  if (done) { reply(action.id,"sent",nullptr,action.request); action.active = false; }
  else { action.phase++; action.next = millis() + ((action.op == "mouse.move" || action.op == "abs.move") ? REPORT_MS : nextDelay); }
}

void setup() {
  Serial0.begin(115200);
  char id[17]; snprintf(id,sizeof(id),"%08lx%08lx",(unsigned long)esp_random(),(unsigned long)esp_random()); bootId = id;
  uint8_t baseMac[6]; esp_read_mac(baseMac, ESP_MAC_BT);
  stableDeviceId = hexBytes(baseMac, sizeof(baseMac));
  NimBLEDevice::init(NAME);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
  NimBLEDevice::setSecurityAuth(true,false,true);
  server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks()); server->advertiseOnDisconnect(true);
  hid = new NimBLEHIDDevice(server);
  hid->setManufacturer("Serial BLE HID Project");
  // Prototype PnP ID, no borrowed commercial keyboard identity.
  uint8_t pnp[] = {1,0xFF,0xFF,1,0,0,1};
  hid->getPnp()->setValue(pnp,sizeof(pnp));
  hid->setHidInfo(0,1);
  hid->setReportMap((uint8_t *)REPORT_MAP,sizeof(REPORT_MAP));
  // Keep the first input characteristic consistent with keyboard-first Report Map.
  keyInput = hid->getInputReport(1); mouseInput = hid->getInputReport(2);
  absInput = hid->getInputReport(4);
  absInput->setCallbacks(new ReportCallbacks());
  uint8_t absZero[5] = {}; absInput->setValue(absZero,5);
  keyInput->setCallbacks(new ReportCallbacks()); mouseInput->setCallbacks(new ReportCallbacks());
  keyInput->setValue(keys,sizeof(keys)); uint8_t mouseZero[4] = {}; mouseInput->setValue(mouseZero,4);
  auto *output = hid->getOutputReport(1); output->setCallbacks(new LedCallbacks()); uint8_t led = 0; output->setValue(&led,1);
  hid->getHidControl()->setCallbacks(new ControlCallbacks());
  // Report protocol only: boot characteristics are intentionally absent.
  hid->startServices(); server->start(); hid->setBatteryLevel(100);
  if (!absInput->getHandle() || absInput->getHandle() == keyInput->getHandle() || absInput->getHandle() == mouseInput->getHandle() || !keyInput->getHandle() || !mouseInput->getHandle() || keyInput->getHandle() == mouseInput->getHandle()) {
    emitEvent("fatal_missing_hid_characteristic");
    while (true) delay(1000);  // Never advertise an incomplete composite HID device.
  }
  auto *adv = NimBLEDevice::getAdvertising();
  adv->setAppearance(HID_KEYBOARD); adv->addServiceUUID(hid->getHidService()->getUUID());
  adv->setName(NAME); adv->enableScanResponse(true); adv->setPreferredParams(12,24);
  NimBLEDevice::startAdvertising();
  emitEvent("boot");
}
void loop() {
  if (disconnected.exchange(false)) {
    abortAction("disconnected_partial_unknown"); emitEvent("ble_disconnected");
  }
  if (absButtons && !action.active && absReady()) sendAbsolute(absX, absY, 0);
  bool kr = ready(true), mr = ready(false);
  if (!kr) keyNeutral = false;
  if (!mr) mouseNeutral = false;
  if (kr && !keyNeutral) { memset(keys,0,sizeof(keys)); keyNeutral = sendKeys(); }
  if (mr && !mouseNeutral) { buttons = 0; mouseNeutral = sendMouse(); }
  if (kr != lastKeyReady || mr != lastMouseReady) {
    lastKeyReady = kr; lastMouseReady = mr; emitEvent("hid_state_changed");
  }
  if (session.length() && millis()-heartbeat > LEASE_MS) {
    abortAction("lease_expired_partial_unknown"); session = ""; emitEvent("lease_expired");
  }
  static String line;
  static bool overflow = false;
  // Bound work per loop to keep input release and action scheduling responsive.
  for (unsigned n = 0; n < 256 && Serial0.available(); n++) {
    char c = Serial0.read();
    if (c == '\n') {
      if (overflow) reply(0,"error","frame_too_long");
      else if (line.length()) handleLine(line);
      line = ""; overflow = false;
    } else if (c != '\r' && !overflow) {
      if (line.length() >= 512 || c == '\0') { overflow = true; line = ""; }
      else line += c;
    }
  }
  advanceAction(); delay(1);
}
