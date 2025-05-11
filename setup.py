from setuptools import setup, find_packages

setup(
    name="holocron",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "numpy",
        "pandas",
        "pinecone",
        "python-dotenv",
        "supabase",
        "pyarrow",
        "boto3"
    ],
    python_requires=">=3.8",
) 