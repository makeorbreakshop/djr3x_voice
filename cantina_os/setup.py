from setuptools import setup, find_packages

setup(
    name="cantina_os",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        # Core dependencies
        "pydantic>=2.5.2",
        "pyee>=11.0.1",
        "python-dotenv>=1.0.0",
        "aiohttp>=3.9.1",
        "httpx>=0.25.0",
        
        # Audio processing
        "sounddevice>=0.4.6",
        "numpy>=1.24.0",
        "deepgram-sdk>=2.12.0",
        "openai-whisper>=20231117",
        "python-vlc>=3.0.20000",
        
        # AI/ML
        "openai>=1.3.7",
        "anthropic>=0.7.7",
        
        # Voice synthesis
        "elevenlabs>=0.2.26",
        "soundfile>=0.12.1",
        
        # Hardware
        "pyserial>=3.5",
        
        # Testing
        "pytest>=7.4.3",
        "pytest-asyncio>=0.21.1",
        "pytest-cov>=4.1.0",
        "aioresponses>=0.7.4",
        "psutil>=5.9.0",
    ],
    python_requires=">=3.11",
) 