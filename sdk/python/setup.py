"""Shipyard Python SDK -- build AI agents for the Shipyard CI/CD pipeline."""

from setuptools import setup, find_packages

setup(
    name="shipyard-sdk",
    version="0.1.0",
    description="Python SDK for the Shipyard agent pipeline",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Shipyard",
    url="https://github.com/AV4TAr/ai-cicd",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "requests>=2.25.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "responses>=0.20",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Software Development :: Build Tools",
    ],
)
