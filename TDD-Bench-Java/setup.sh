git clone https://github.com/amazon-science/SWE-PolyBench.git
cd SWE-PolyBench
git checkout 1963184
git apply --whitespace=nowarn ../tdd_java.diff
rm -rf README.md
cd ..
cp -r SWE-PolyBench/. .
rm -rf SWE-PolyBench
pip install -e .



