#!/usr/bin/env python3
import ctypes
import json
import os
import sys
import time
import struct
import platform
import signal
import resource

def get_online_cpus():
    try:
        with open('/sys/devices/system/cpu/online', 'r') as f:
            line = f.read().strip()
        cpus = []
        for part in line.split(','):
            if '-' in part:
                start, end = part.split('-')
                cpus.extend(range(int(start), int(end) + 1))
            else:
                cpus.append(int(part))
        return cpus
    except Exception:
        return list(range(os.cpu_count() or 1))

arch = platform.machine()
if arch == 'x86_64':
    SYS_perf_event_open = 298
elif arch == 'aarch64':
    SYS_perf_event_open = 241
else:
    print('ERROR: unsupported architecture %s' % arch)
    sys.exit(1)

libc = ctypes.CDLL(None, use_errno=True)

PERF_EVENT_IOC_ENABLE  = 0x2400
PERF_EVENT_IOC_DISABLE = 0x2401
PERF_EVENT_IOC_RESET   = 0x2403

def make_attr(type_, config, read_format, flags):
    attr = struct.pack('<IIQQQQQ', type_, 120, config, 0, 0, read_format, flags)
    attr += b'\x00' * (120 - len(attr))
    return attr

def emit(out, rec):
    out.write(json.dumps(rec) + '\n')

def main():
    if len(sys.argv) < 2:
        print('Usage: kerneltools-hw-counters.py <interval_seconds> [output_file] [cpu_list]')
        print('  cpu_list: comma-separated CPUs or ranges, e.g. 192-207,576-591')
        sys.exit(1)

    try:
        _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if _soft < 8192:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(8192, _hard), _hard))
    except Exception:
        pass

    interval = float(sys.argv[1])
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'hw-counters-stdout.txt'

    cpus = get_online_cpus()

    if len(sys.argv) > 3 and sys.argv[3]:
        cpu_filter = set()
        for part in sys.argv[3].split(','):
            part = part.strip()
            if not part:
                continue
            if '-' in part:
                lo, hi = part.split('-')
                cpu_filter.update(range(int(lo), int(hi) + 1))
            else:
                cpu_filter.add(int(part))
        cpus = [c for c in cpus if c in cpu_filter]
        print('CPU filter active: monitoring %d CPUs' % len(cpus))

    print('Monitoring %d CPUs at interval %ss' % (len(cpus), interval))

    cpu_fds = {}
    read_format = 7
    attr_cycles = make_attr(0, 0, read_format, 3)
    attr_instr  = make_attr(0, 1, read_format, 2)
    attr_miss   = make_attr(0, 3, read_format, 2)
    attr_back   = make_attr(0, 8, read_format, 2)
    attr_front  = make_attr(0, 7, read_format, 2)
    attrs = [attr_cycles, attr_instr, attr_miss, attr_back, attr_front]

    opened_count = 0
    for cpu in cpus:
        fds = []
        leader_fd = -1
        success = True
        for i, attr in enumerate(attrs):
            group_fd = leader_fd if i > 0 else -1
            fd = libc.syscall(SYS_perf_event_open, attr, -1, cpu, group_fd, 0)
            if fd < 0:
                err = ctypes.get_errno()
                print('WARNING: failed to open event %d on CPU %d, errno %d: %s' % (i, cpu, err, os.strerror(err)))
                success = False
                break
            fds.append(fd)
            if i == 0:
                leader_fd = fd
        if success:
            cpu_fds[cpu] = fds
            opened_count += 1
        else:
            for fd in fds:
                try: os.close(fd)
                except Exception: pass

    if opened_count == 0:
        print('ERROR: failed to open any performance counters. Are you root?')
        sys.exit(1)

    for cpu, fds in cpu_fds.items():
        libc.ioctl(fds[0], PERF_EVENT_IOC_RESET, 0)
        libc.ioctl(fds[0], PERF_EVENT_IOC_ENABLE, 0)

    print('Successfully started hardware performance counters on %d CPUs.' % opened_count)

    UMC_CAS_ALL_EVENT = 0x0a
    UMC_BYTES_PER_CAS = 64
    umc_fds = {}
    prev_umc = {}
    umc_base = '/sys/bus/event_source/devices'
    if os.path.isdir(umc_base):
        for umc_name in sorted(os.listdir(umc_base)):
            if not umc_name.startswith('amd_umc_'):
                continue
            try:
                pmu_path = os.path.join(umc_base, umc_name)
                pmu_type = int(open(os.path.join(pmu_path, 'type')).read().strip())
                cpu = int(open(os.path.join(pmu_path, 'cpumask')).read().strip().split(',')[0].split('-')[0])
                attr = struct.pack('<IIQQQQQ', pmu_type, 120, UMC_CAS_ALL_EVENT, 0, 0, 0, 1)
                attr += b'\x00' * (120 - len(attr))
                fd = libc.syscall(SYS_perf_event_open, attr, -1, cpu, -1, 0)
                if fd < 0:
                    continue
                libc.ioctl(fd, PERF_EVENT_IOC_RESET, 0)
                libc.ioctl(fd, PERF_EVENT_IOC_ENABLE, 0)
                umc_fds[umc_name] = fd
                prev_umc[umc_name] = 0
            except Exception as e:
                print('WARNING: UMC %s: %s' % (umc_name, e))
        print('Opened %d AMD UMC CAS counters' % len(umc_fds))

    running = True
    def stop_handler(signum, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    prev_values = {cpu: [0.0, 0.0, 0.0, 0.0, 0.0] for cpu in cpu_fds}

    with open(output_file, 'w') as out:
        emit(out, {'ts_ms': 0, 'kind': 'meta', 'interval_sec': interval,
                   'cpu_count': opened_count, 'umc_count': len(umc_fds)})
        out.flush()

        while running:
            time.sleep(interval)
            now_ms = int(round(time.time() * 1000))

            for cpu, fds in cpu_fds.items():
                try:
                    data = os.read(fds[0], 64)
                    if len(data) < 64:
                        continue
                    unpacked = struct.unpack('<QQQQQQQQ', data)
                    values = unpacked[3:8]
                    deltas = []
                    prev = prev_values[cpu]
                    for idx, val in enumerate(values):
                        delta = val - prev[idx]
                        if delta < 0:
                            delta = val
                        deltas.append(delta)
                        prev[idx] = val
                    emit(out, {'ts_ms': now_ms, 'kind': 'cpu', 'cpu': cpu,
                               'cycles': deltas[0], 'instructions': deltas[1],
                               'cache_misses': deltas[2], 'stall_backend': deltas[3],
                               'stall_frontend': deltas[4]})
                except Exception as e:
                    print('WARNING: error reading CPU %d: %s' % (cpu, e))

            for umc_name, umc_fd in umc_fds.items():
                try:
                    raw = os.read(umc_fd, 8)
                    if len(raw) < 8:
                        continue
                    count = struct.unpack('<Q', raw)[0]
                    delta = max(0, count - prev_umc[umc_name])
                    prev_umc[umc_name] = count
                    emit(out, {'ts_ms': now_ms, 'kind': 'umc', 'umc': umc_name,
                               'cas_delta': delta, 'bytes': delta * UMC_BYTES_PER_CAS})
                except Exception as e:
                    print('WARNING: reading %s: %s' % (umc_name, e))

            out.flush()

    print('Shutting down counters...')
    for cpu, fds in cpu_fds.items():
        libc.ioctl(fds[0], PERF_EVENT_IOC_DISABLE, 0)
        for fd in fds:
            try: os.close(fd)
            except Exception: pass
    for umc_fd in umc_fds.values():
        try:
            libc.ioctl(umc_fd, PERF_EVENT_IOC_DISABLE, 0)
            os.close(umc_fd)
        except Exception: pass
    print('Shutdown complete.')

if __name__ == '__main__':
    main()
