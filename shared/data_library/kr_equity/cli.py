"""kr_equity Linux 단계 실행 — `python -m shared.data_library.kr_equity.cli <step>`  (step 은 sources.STEPS 의 linux 단계)"""
import argparse
import os
import sys

from . import sources


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("step", choices=sorted(sources.LINUX_STEPS))
    a = ap.parse_args(argv)
    os.makedirs(sources.path("logs"), exist_ok=True)
    ok = sources.run(a.step)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
