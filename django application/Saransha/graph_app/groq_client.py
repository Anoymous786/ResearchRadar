from groq import Groq

import os

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# Keep client creation lazy so missing env var doesn't crash imports.
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

def generate_ai_response(user_input, system_prompt="You are a helpful AI project assistant."):
    try:
        if client is None:
            return "Error: GROQ_API_KEY is not configured. Set it in your environment to use AI features."

        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            model="llama3-8b-8192",
        )

        return chat_completion.choices[0].message.content

    except Exception as e:
        return f"Error: {str(e)}"