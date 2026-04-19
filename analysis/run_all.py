"""Run all P1-P18 analysis scripts in sequential batches using subprocess."""
import subprocess, time, threading
from pathlib import Path

PYTHON = r'C:\Users\BT-25\AppData\Local\Programs\Python\Python3127\python.exe'
SCRIPTS_DIR = Path(r'C:\Users\BT-25\Desktop\project\dhan-trader\analysis')

if __name__ == '__main__':
    scripts = sorted(SCRIPTS_DIR.glob('p*.py'))
    # exclude run_all.py itself
    scripts = [s for s in scripts if 'run_all' not in s.name]
    print(f"Found {len(scripts)} scripts to run")

    BATCH = 2  # 2 at a time to avoid OOM (each ~1.5GB)
    results = []
    t_start = time.time()

    def run_one(path):
        t0 = time.time()
        name = path.name
        try:
            r = subprocess.run([PYTHON, str(path)], capture_output=True, text=True, timeout=1200)
            elapsed = time.time() - t0
            last = r.stdout.strip().split('\n')[-1] if r.stdout.strip() else ''
            if r.returncode == 0:
                return f"  OK  {name:<35} {elapsed:>6.0f}s  {last}"
            else:
                err = r.stderr.strip().split('\n')[-1][:80] if r.stderr else 'unknown'
                return f"  ERR {name:<35} {elapsed:>6.0f}s  {err}"
        except subprocess.TimeoutExpired:
            return f"  TMO {name:<35}"
        except Exception as e:
            return f"  EXC {name:<35} {e}"

    for i in range(0, len(scripts), BATCH):
        batch = scripts[i:i+BATCH]
        bn = i//BATCH + 1
        tb = (len(scripts)+BATCH-1)//BATCH
        print(f"\nBatch {bn}/{tb}: {', '.join(s.name for s in batch)}")

        threads = []
        batch_results = [None] * len(batch)

        def worker(idx, path):
            batch_results[idx] = run_one(path)

        for j, s in enumerate(batch):
            t = threading.Thread(target=worker, args=(j, s))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        for r in batch_results:
            if r:
                print(r)
                results.append(r)

    print(f"\n{'='*70}")
    print(f"ALL DONE in {time.time()-t_start:.0f}s")
    print(f"{'='*70}")
    for r in sorted(results):
        print(r)
