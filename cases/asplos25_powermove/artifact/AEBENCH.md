# PowerMove artifact evaluation

Install the evaluation dependencies from `requirements-evaluation.txt`, then run:

```bash
python3 run_evaluation.py
```

The runner uses five released inputs from the paper's Figure 6 evaluation and writes one JSON result and one complete log per workload under `evaluation/`. It evaluates PowerMove with and without the storage zone, and also runs Enola for QAOA-regular3-30 as a bounded continuous-router comparison. The QFT workload is the longest and can take several minutes.
