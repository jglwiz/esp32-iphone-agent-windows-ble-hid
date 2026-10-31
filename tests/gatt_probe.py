"""Read-only GATT diagnostic. Run only when the phone is disconnected; never sends HID input."""
import asyncio
import json
from bleak import BleakClient, BleakScanner


async def main():
    device = await BleakScanner.find_device_by_name('S3-iPhone-HID', timeout=8)
    if device is None:
        raise RuntimeError('Peripheral not advertising')
    async with BleakClient(device, timeout=15) as client:
        for service in client.services:
            if service.uuid.startswith(('00001812', '0000180a')):
                for characteristic in service.characteristics:
                    row = {'service': service.uuid, 'uuid': characteristic.uuid,
                           'handle': characteristic.handle, 'properties': characteristic.properties}
                    try:
                        if 'read' in characteristic.properties:
                            row['value'] = (await client.read_gatt_char(characteristic)).hex()
                        row['descriptors'] = []
                        for descriptor in characteristic.descriptors:
                            row['descriptors'].append({'uuid': descriptor.uuid,
                                                       'value': (await client.read_gatt_descriptor(descriptor.handle)).hex()})
                    except Exception as exc:
                        row['error'] = str(exc)
                    print(json.dumps(row), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
