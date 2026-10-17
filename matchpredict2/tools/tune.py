import itertools, time, numpy as np
from predictor.engine.backtest import backtest_season
seasons=range(2015,2025)
for hl,rg in itertools.product([1.0,1.5,2.5,4.0],[3,8,20]):
    t=time.time(); res=[backtest_season(s,1,hl,rg) for s in seasons]
    print(f"hl={hl} ridge={rg}: ll={np.mean([r['log_loss'] for r in res]):.4f} rps={np.mean([r['rps'] for r in res]):.4f} acc={np.mean([r['accuracy'] for r in res]):.3f} base_ll={np.mean([r['baseline_log_loss'] for r in res]):.4f} ({time.time()-t:.0f}s)", flush=True)
