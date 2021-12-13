#!/usr/bin/env python3
import argparse
import sys

def main(args):
    '''
    We need to be able select a time range inside of a perf report.
    We have the time range in milliseconds since the unix epoc
    we need the offsect in seconds.nanoseconds since the boot of the system
    these are the conversions. The variable names are meant to inidicate
    the unit of time and source of stamp.
    '''

    # convert seconds to milliseconds
    boot_msec_epoc = args.boot_time * 1_000

    # Convert first sample to milliseconds
    perf_first_sample_msec = args.perf_first_sample * 1_000

    # time of first recording in epoch milliseconds
    perf_first_sample_msec_epoc = boot_msec_epoc + perf_first_sample_msec

    # how many milliseconds into the perf recording is my sample
    crucible_sample_start_msec_offset = args.crucible_sample_start - perf_first_sample_msec_epoc
    crucible_sample_stop_msec_offset = args.crucible_sample_stop - perf_first_sample_msec_epoc

    # Convert msec offset to seconds.nanoseconds (but we won't have nanosecond precision)
    crucible_sample_start_sec_nsec = crucible_sample_start_msec_offset / 1_000
    crucible_sample_stop_sec_nsec = crucible_sample_stop_msec_offset / 1_000

    # Finally add crucible values to perf first sample to get desired offsets
    final_start_sec_nsec = args.perf_first_sample + crucible_sample_start_sec_nsec
    final_stop_sec_nsec = args.perf_first_sample + crucible_sample_stop_sec_nsec

    print(f"perf report --time {format(final_start_sec_nsec,'.6g')},{format(final_stop_sec_nsec, '.6g')}")
    sys.exit(0)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
            description = 'Create harmony between crucible epoc time and perf time')

    parser.add_argument('-b','--sys-boot-time',
            type = int, required = True, dest = 'boot_time',
            help = 'Boot time in seconds since the unix epoch of the system that perf ran on.\
                    The expected way to find this is '+ '''date -u --date="$(uptime -s)" +%%s''')
    parser.add_argument('-f','--first-sample',
            type = float, required = True, dest = 'perf_first_sample',
            help = 'perf reports the time of first sample in seconds.nanoseconds since system boot\
                    use perf report --header-only to get this value')
    parser.add_argument('-s1','--sample-start',
            type = int, required = True, dest = 'crucible_sample_start',
            help = 'the start time in milliseconds since epoc of the desired\
                    sample as reported by crucible')
    parser.add_argument('-s2','--sample-stop',
            type = int, required = True, dest = 'crucible_sample_stop',
            help = 'the stop time in milliseconds since epoc of the desired\
                    sample as reported by crucible')
    args = parser.parse_args()
    main(args)
