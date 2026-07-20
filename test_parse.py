import re, subprocess
result = subprocess.run(['v4l2-ctl', '--list-formats-ext'], capture_output=True, text=True)
for line in result.stdout.split('\n'):
    if '2304' in line or 'Interval' in line[:40]:
        print(repr(line))
