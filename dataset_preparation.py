from datasets import load_dataset

swebench = load_dataset('princeton-nlp/SWE-bench_Verified', split='test')

keep=[]
with open("id_list.txt","r") as f:
    lines=f.readlines()
    for ln in lines:
        keep.append(ln.strip())

filtered_swebench = swebench.filter(lambda x: x['instance_id'] in keep)
df = filtered_swebench.to_pandas()
df = df.drop(columns=['PASS_TO_PASS', 'FAIL_TO_PASS'])
df.to_json('TDD_Bench.json', orient='records')



