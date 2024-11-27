TDD-Bench-Verified is a new benchmark for generating test cases for test-driven development (TDD). Test-driven development, or TDD, is the practice of "test first, write code later", where a software developer writes tests before writing corresponding code. This means the tests initially fail, and, if everything goes right, they pass after applying the code changes. Compared to the common practice of "write first, test later", TDD makes requirements clearer, enhances confidence in the code once written, and leads to tests that emphasize the interface over implementation details.

TDD-Bench-Verified is derived from SWE-bench Verified. Each instance $x = (d_{issue}, c_{old})$ comprises a natural-language issue description $d_{issue}$ together with the original version of a codebase $c_{old}$ right before the issue was addressed. A prediction $y$ for an instance consists of a set of tests that should fail on $c_{old}$ and pass on $c_{new}$. However, solutions to TDD-Bench-Verified should predict $y$ without looking at $c_{new}$. This is a challenging task for large language models (LLMs). TDD-Bench-Verified contains 449 instance $x_i$, along with a Docker-based evaluation harness that evaluates a submission of predictions $y_i$. It checks the fail-to-pass criterion for each $y_i$, as well as measuring its code coverage on the code change from $c_{old}$ to $c_{new}$.

**Paper Link: TBA** 

**Leaderboard Link: TBA** 



## 🚀 Set Up
TDD-bench uses Docker for reproducible evaluations just like SWE-Bench.
Follow the instructions in the [Docker setup guide](https://docs.docker.com/engine/install/) to install Docker on your machine. For additional assistance, you can also refer to the [SWE-Bench](https://github.com/princeton-nlp/SWE-bench) repository.

Finally, to build TDD-bench from source, follow these steps:
```bash
git clone https://github.ibm.com/tfahmed/TDD-Bench-Verified.git
cd TDD-Bench-Verified
pip install -e .
```

Generate TDD_Bench.json by running the following command. This json file will contain the complete dataset (which includes repository name, issue description, base commit SHA and other relevant information for 449 instances).
```bash
python dataset_preparation.py
```


Test your installation by running:
```bash
python -m tddbench.harness.run_evaluation \
    --predictions_path gold \
    --max_workers 1 \
    --instance_ids astropy__astropy-14995 \
    --run_id validate-gold
```


Evaluate model predictions on TDD-bench using the evaluation harness with the following command. This command will take the model generated test patches (--predictions_path) as input and report the $TDD_Score$ and number of fail-to-pass instances.
```bash
python -m tddbench.harness.run_evaluation \
    --dataset_name TDD_Bench.json \  
    --predictions_path <path_to_predictions> \
    --max_workers <num_workers> \
    --run_id <run_id>
#use --predictions_path 'gold' to verify the gold patches
#use --run_id to name the evaluation run

```


Use golden_test_patch.json formatting as a reference for "--predictions_path". The format is also shown below. 

```bash
[
    {
        "instance_id": "astropy__astropy-12907",
        "model_patch": "diff --git a/astropy/modeling/tests/test_separable.py b/astropy/modeling/tests/test_separable.py\n--- a/astropy/modeling/tests/test_separable.py\n+++ b/astropy/modeling/tests/test_separable.py\n@@ -28,6 +28,13 @@\n p1 = models.Polynomial1D(1, name='p1')\n \n \n+cm_4d_expected = (np.array([False, False, True, True]),\n+                  np.array([[True,  True,  False, False],\n+                            [True,  True,  False, False],\n+                            [False, False, True,  False],\n+                            [False, False, False, True]]))\n+\n+\n compound_models = {\n     'cm1': (map3 & sh1 | rot & sh1 | sh1 & sh2 & sh1,\n             (np.array([False, False, True]),\n@@ -52,7 +59,17 @@\n     'cm7': (map2 | p2 & sh1,\n             (np.array([False, True]),\n              np.array([[True, False], [False, True]]))\n-            )\n+            ),\n+    'cm8': (rot & (sh1 & sh2), cm_4d_expected),\n+    'cm9': (rot & sh1 & sh2, cm_4d_expected),\n+    'cm10': ((rot & sh1) & sh2, cm_4d_expected),\n+    'cm11': (rot & sh1 & (scl1 & scl2),\n+             (np.array([False, False, True, True, True]),\n+              np.array([[True,  True,  False, False, False],\n+                        [True,  True,  False, False, False],\n+                        [False, False, True,  False, False],\n+                        [False, False, False, True,  False],\n+                        [False, False, False, False, True]]))),\n }\n \n \n"
    },
    {
        "instance_id": "astropy__astropy-13033",
        "model_patch": "diff --git a/astropy/timeseries/tests/test_sampled.py b/astropy/timeseries/tests/test_sampled.py\n--- a/astropy/timeseries/tests/test_sampled.py\n+++ b/astropy/timeseries/tests/test_sampled.py\n@@ -395,6 +395,14 @@ def test_required_columns():\n     assert exc.value.args[0] == (\"TimeSeries object is invalid - expected \"\n                                  \"'time' as the first column but found 'banana'\")\n \n+    # https://github.com/astropy/astropy/issues/13009\n+    ts_2cols_required = ts.copy()\n+    ts_2cols_required._required_columns = ['time', 'a']\n+    with pytest.raises(ValueError) as exc:\n+        ts_2cols_required.remove_column('a')\n+    assert exc.value.args[0] == (\"TimeSeries object is invalid - expected \"\n+                                 \"['time', 'a'] as the first columns but found ['time', 'b']\")\n+\n \n @pytest.mark.parametrize('cls', [BoxLeastSquares, LombScargle])\n def test_periodogram(cls):\n"
    },
]
```


All the experiments were done using python 3.12.4. The requirement is Python 3.11 or later. List of Pre-Requisites:
```bash
beautifulsoup4
datasets
docker
ghapi
python-dotenv
requests
unidiff
tqdm
pytest
cldk
```


