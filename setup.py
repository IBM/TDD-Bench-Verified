import setuptools

with open('README.md', 'r', encoding='utf-8') as fh:
    long_description = fh.read()

setuptools.setup(
    name='tddbench',
    packages=setuptools.find_packages(),
    python_requires='>=3.11',
    install_requires=[
        'beautifulsoup4',
        'datasets',
        'docker',
        'ghapi',
        'python-dotenv',
        'requests',
        'unidiff',
        'tqdm',
        'pytest',
        'cldk @ git+https://github.com/IBM/codellm-devkit.git@4f513bddd0a1c5475fe6fc7a91496e582c905214'
    ],
    include_package_data=True,
)
