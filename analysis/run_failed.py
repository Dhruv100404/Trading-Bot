import subprocess, time, threading
from pathlib import Path

PYTHON = r'C:\Users\BT-25\AppData\Local\Programs\Python\Python3127\python.exe'
D = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\analysis')

failed = ['p3_buy_ratio_pattern.py','p4_volume_pattern.py','p9_day_context.py',
          'p11_gap_fill_timing.py','p12_relative_strength.py','p13_consecutive_gaps.py',
          'p15_loser_autopsy.py','p16_sector_clustering.py','p17_mfe_mae_ratio.py','p18_opening_auction.py']

if __name__ == '__main__':
    print(f"Re-running {len(failed)} failed scripts in batches of 2")
    t0 = time.time()

    def run(name):
        t = time.time()
        r = subprocess.run([PYTHON, str(D/name)], capture_output=True, text=True, timeout=1200)
        el = time.time()-t
        last = r.stdout.strip().split('\n')[-1] if r.stdout.strip() else ''
        if r.returncode == 0:
            return f"  OK  {name:<40} {el:>5.0f}s  {last}"
        err = r.stderr.strip().split('\n')[-1][:100] if r.stderr else 'unknown'
        return f"  ERR {name:<40} {el:>5.0f}s  {err}"

    for i in range(0, len(failed), 2):
        batch = failed[i:i+2]
        print(f"\nBatch: {', '.join(batch)}")
        results = [None]*len(batch)
        def worker(idx, name): results[idx] = run(name)
        threads = [threading.Thread(target=worker, args=(j,n)) for j,n in enumerate(batch)]
        for t in threads: t.start()
        for t in threads: t.join()
        for r in results:
            if r: print(r)

    print(f"\nAll done in {time.time()-t0:.0f}s")
