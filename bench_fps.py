import time, datetime
start = time.time()
count = 0
while time.time() - start < 5:
    count += 1
    time.sleep(0.01)  # simulate minimal work
elapsed = time.time() - start
print(f'Max possible rate: {count/elapsed:.0f} fps (no actual work)')
