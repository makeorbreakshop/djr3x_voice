from openai import OpenAI
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get API key
api_key = os.getenv("OPENAI_API_KEY")

try:
    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)
    print("OpenAI client initialized successfully!")
    
    # Try a simple API call
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Say hello!"}]
    )
    print("Response:", response.choices[0].message.content)
    
except Exception as e:
    print("Error:", str(e)) 