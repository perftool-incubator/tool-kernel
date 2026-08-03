#!/usr/bin/env python3
import ctypes
import os
import sys
import time
import struct
import platform
import signal

def get_online_cpus():
    try:
        with open("/sys/devices/system/cpu/online", "r") as f:
            line = f.read().strip()
        # Parse ranges like "0-3" or "0,2-4"
        cpus = []
        for part in line.split(","):
            if "-" in part:
                start, end = part.split("-")
                cpus.extend(range(int(start), int(end) + 1))
            else:
                cpus.append(int(part))
        return cpus
    except Exception:
        return list(range(os.cpu_count() or 1))

# Detect architecture
arch = platform.machine()
if arch == "x86_64":
    SYS_perf_event_open = 298
elif arch == "aarch64":
    SYS_perf_event_open = 241
else:
    print(f"ERROR: unsupported architecture {arch}")
    sys.exit(1)

libc = ctypes.CDLL(None, use_errno=True)

# ioctl calls
PERF_EVENT_IOC_ENABLE = 0x2400
PERF_EVENT_IOC_DISABLE = 0x2401
PERF_EVENT_IOC_RESET = 0x2403

def make_attr(type_, config, read_format, flags):
    # struct perf_event_attr: type (u32), size (u32), config (u64), sample_period (u64), sample_type (u64), read_format (u64), flags (u64)
    # size is 120 bytes
    attr = struct.pack("<IIQQQQQ", type_, 120, config, 0, 0, read_format, flags)
    attr += b"\x00" * (120 - len(attr))
    return attr

def main():
    if len(sys.argv) < 2:
        print("Usage: kerneltools-hw-counters.py <interval_seconds> [output_file]")
        sys.exit(1)

    interval = float(sys.argv[1])
    output_file = sys.argv[2] if len(sys.argv) > 2 else "hw-counters-stdout.txt"

    cpus = get_online_cpus()
    print(f"Monitoring CPUs: {cpus} at interval {interval}s")

    # Each CPU has a list of fds: [leader_fd, instruction_fd, cache_miss_fd, backend_fd, frontend_fd]
    cpu_fds = {}
    
    # Read format: PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING = 7
    # Read size is 3 * 8 + 5 * 8 = 64 bytes
    read_format = 7
    
    # Attributes
    # flags=3 -> disabled=1, inherit=1
    attr_cycles = make_attr(0, 0, read_format, 3) # Cycles
    attr_instr  = make_attr(0, 1, read_format, 2) # Instructions
    attr_miss   = make_attr(0, 3, read_format, 2) # Cache misses
    attr_back   = make_attr(0, 8, read_format, 2) # Stalled backend
    attr_front  = make_attr(0, 7, read_format, 2) # Stalled frontend

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
                print(f"WARNING: failed to open event {i} on CPU {cpu}, errno {err}: {os.strerror(err)}")
                success = False
                break
            fds.append(fd)
            if i == 0:
                leader_fd = fd
                
        if success:
            cpu_fds[cpu] = fds
            opened_count += 1
        else:
            # Clean up partial group
            for fd in fds:
                try:
                    os.close(fd)
                except Exception:
                    pass

    if opened_count == 0:
        print("ERROR: failed to open any performance counters. Are you root?")
        sys.exit(1)

    # Enable all groups
    for cpu, fds in cpu_fds.items():
        leader_fd = fds[0]
        libc.ioctl(leader_fd, PERF_EVENT_IOC_RESET, 0)
        libc.ioctl(leader_fd, PERF_EVENT_IOC_ENABLE, 0)

    print(f"Successfully started hardware performance counters on {opened_count} CPUs.")

    # Graceful stop handler
    running = True
    def stop_handler(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    # Keep track of previous absolute read values to compute delta per interval
    prev_values = {cpu: [0.0, 0.0, 0.0, 0.0, 0.0] for cpu in cpu_fds}

    with open(output_file, "w") as out:
        out.write("# timestamp_ms, cpu, cycles, instructions, cache_misses, stall_backend, stall_frontend\n")
        out.flush()

        while running:
            # Align sleep time slightly to interval boundaries
            time.sleep(interval)
            
            # Read and record
            now_ms = int(round(time.time() * 1000))
            
            for cpu, fds in cpu_fds.items():
                leader_fd = fds[0]
                try:
                    # Read 64 bytes
                    data = os.read(leader_fd, 64)
                    if len(data) < 64:
                        continue
                    unpacked = struct.unpack("<QQQQQQQQ", data)
                    
                    values = unpacked[3:8]
                    
                    # Compute deltas
                    deltas = []
                    prev = prev_values[cpu]
                    for idx, val in enumerate(values):
                        delta = val - prev[idx]
                        if delta < 0:
                            delta = val
                        deltas.append(delta)
                        prev[idx] = val
                    
                    # Write to file
                    out.write(f"{now_ms},CPU{cpu},{deltas[0]},{deltas[1]},{deltas[2]},{deltas[3]},{deltas[4]}\n")
                except Exception as e:
                    print(f"WARNING: error reading CPU {cpu}: {e}")
                    
            out.flush()

    # Disable and close
    print("Shutting down counters...")
    for cpu, fds in cpu_fds.items():
        leader_fd = fds[0]
        libc.ioctl(leader_fd, PERF_EVENT_IOC_DISABLE, 0)
        for fd in fds:
            try:
                os.close(fd)
            except Exception:
                pass

    print("Shutdown complete.")

if __name__ == "__main__":
    main()
