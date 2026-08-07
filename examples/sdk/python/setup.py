from setuptools import setup, find_packages

setup(
    name="openask-sdk",
    version="0.1.0",
    description="OpenAsk AI Knowledge Base SDK",
    packages=find_packages(),
    install_requires=["requests>=2.28.0"],
    python_requires=">=3.8",
)