"""Absolute pointer controller. Device transport is injected by the serial owner."""
import json
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
from pointer_vision import locate


def run(target, output, perform, initial_gain=2.9, click=False):
    if not (0 <= target[0] < 496 and 0 <= target[1] < 1080):
        raise ValueError('target outside screenshot')
    output.mkdir(parents=True, exist_ok=False)
    log = {'target': list(target), 'initial_gain': initial_gain,
           'evidence_directory': str(output.resolve()),
           'tolerance_px': 6, 'click_requested': click,
           'status': 'failed', 'observations': []}
    started = perf_counter()
    count = 0

    def frame(dx, dy, name):
        nonlocal count
        count += 1
        path = output / f'{name}.jpg'
        result = perform([{'op': 'move', 'dx': int(dx), 'dy': int(dy)}], path)
        if (result.get('released') is not True or not result.get('frame')
                or result.get('capture_error')
                or result.get('input_state') != 'reports_sent'):
            raise RuntimeError('action_or_capture_failed: inspect saved result; no replay')
        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError('image_missing')
        return image

    try:
        # Bounded probes recover from an unknown initial edge without human setup.
        anchor = frame(1, 0, 'anchor')
        for probe_index, (px, py) in enumerate([(-25, 0), (50, 0), (0, -25), (0, 50)]):
            current = frame(px, py, f'acquire{probe_index}')
            observation = locate(anchor, current, px, py)
            log['observations'].append({'stage': 'acquire', 'probe': [px, py], **observation})
            if observation['status'] == 'estimated':
                break
            if observation.get('reason') == 'scene_changed':
                raise RuntimeError('acquire_scene_changed')
            anchor = current
        if observation['status'] != 'estimated':
            raise RuntimeError('acquire_unknown')
        anchor_position = np.array(observation['old_center'])
        position = np.array([observation['x'], observation['y']])
        target = np.array(target, dtype=float)
        if np.linalg.norm(target - anchor_position) < 55:
            # Move away from the target to obtain a usable reference footprint.
            px = -35 if target[0] > 248 else 35
            anchor = frame(px, 0, 'rebase_anchor')
            current = frame(-px, 0, 'rebase_acquire')
            observation = locate(anchor, current, -px, 0)
            log['observations'].append({'stage': 'rebase', **observation})
            if observation['status'] != 'estimated':
                raise RuntimeError('rebase_unknown')
            anchor_position = np.array(observation['old_center'])
            position = np.array([observation['x'], observation['y']])
            if np.linalg.norm(target - anchor_position) < 55:
                raise RuntimeError('rebase_too_close')
        gain = initial_gain
        move_budget = min(16, max(5, int(np.ceil(np.max(np.abs(target-position)) / (55*min(gain, 2))))+3))
        log['move_budget'] = move_budget
        stalled = 0
        for iteration in range(move_budget+1):
            error = target - position
            distance = float(np.linalg.norm(error))
            log['observations'][-1]['error_px'] = distance
            if distance <= 6:
                log.update(status='reached', final_position=position.tolist())
                marked = current.copy()
                cv2.circle(marked, tuple(target.astype(int)), 6, (255, 0, 0), 1)
                cv2.drawMarker(marked, tuple(np.rint(position).astype(int)),
                               (0, 0, 255), cv2.MARKER_CROSS, 14, 1)
                cv2.imwrite(str(output / 'final-marked.jpg'), marked)
                if click:
                    # Fresh observation must agree before committing the click.
                    count += 1
                    verify_path = output / 'preclick.jpg'
                    verify = perform([], verify_path)
                    if not verify.get('frame') or verify.get('capture_error') or not verify.get('released'):
                        raise RuntimeError('preclick_capture_failed')
                    measured = locate(anchor, cv2.imread(str(verify_path)), *(position-anchor_position))
                    log['preclick'] = measured
                    if measured['status'] != 'estimated':
                        raise RuntimeError('preclick_position_unknown')
                    if (np.linalg.norm(np.array([measured['x'], measured['y']])-target) > 6
                            or np.linalg.norm(np.array(measured['old_center'])-anchor_position) > 5):
                        raise RuntimeError('preclick_position_changed')
                    count += 1
                    log['click_attempted'] = True
                    clicked = perform([{'op': 'click', 'button': 1}], output / 'clicked.jpg', settle_ms=1000)
                    log['click_result'] = clicked
                    # Report submission only; a human checks actual UI effect.
                    log['status'] = 'click_submitted' if (
                        clicked.get('released') and clicked.get('input_state') == 'reports_sent'
                        and clicked.get('frame') and not clicked.get('capture_error')) else 'click_outcome_unknown'
                break
            if iteration == move_budget:
                raise RuntimeError('correction_limit')
            command = np.clip(np.rint(error / gain), -55, 55).astype(int)
            if not command.any():
                raise RuntimeError('zero_command_outside_tolerance')
            expected = position + command * gain - anchor_position
            current = frame(*command, f'move{iteration}')
            observation = locate(anchor, current, *expected)
            log['observations'].append({'stage': f'move{iteration}',
                                        'command': command.tolist(), 'gain': gain,
                                        **observation})
            if observation['status'] != 'estimated':
                raise RuntimeError('tracking_unknown')
            # The unchanged anchor footprint must stay consistent.
            if np.linalg.norm(np.array(observation['old_center']) - anchor_position) > 5:
                raise RuntimeError('anchor_changed')
            new_position = np.array([observation['x'], observation['y']])
            stalled = stalled + 1 if np.linalg.norm(new_position-position) < 1.5 else 0
            log['observations'][-1]['stalled_count'] = stalled
            if stalled >= 2:
                raise RuntimeError('no_progress: two consecutive moves below 1.5 px')
            if np.linalg.norm(command) >= 10:
                measured_gain = float((new_position - position) @ command / (command @ command))
                if 1 <= measured_gain <= 5:
                    gain = measured_gain
            position = new_position
    except Exception as exc:
        log['status'] = 'click_outcome_unknown' if log.get('click_attempted') else 'failed'
        log['reason'] = str(exc)
    finally:
        log.update(elapsed_seconds=perf_counter()-started, requests=count)
        (output / 'summary.json').write_text(json.dumps(log, indent=2), encoding='utf-8')
    return log

