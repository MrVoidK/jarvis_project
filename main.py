import ollama
from audio_handler import transcribe

# The core identity and behavioral rules for the AI (Jarvis Persona)
SYSTEM_PROMPT = """You are JARVIS, a highly advanced, efficient, and concise AI assistant.
Your primary directive is to assist the user quickly and accurately.
Rules:
1. Always respond STRICTLY in English.
2. Keep your answers extremely concise and direct (1-2 sentences maximum unless asked for details).
3. Do not use markdown formatting (like ** or *) in your output, as it will be read aloud by a TTS engine.
4. Maintain a professional, slightly dry, but helpful tone."""

# We are using Llama 3.1 (8B) as our local brain
MODEL_NAME = "llama3.1" 

def think_and_respond(user_input):
    """Sends the user input to the local Llama 3.1 model and returns the response."""
    print("\n[JARVIS IS THINKING...]")
    
    try:
        response = ollama.chat(
            model=MODEL_NAME, 
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_input}
            ]
        )
        return response['message']['content'].strip()
    
    except Exception as e:
        return f"System error during cognitive processing: {str(e)}"

def run_jarvis():
    """The main execution loop for the MVP pipeline (Ears -> Brain)."""
    print("=== PROJECT JARVIS MVP ONLINE ===")
    
    # Step 1: Listen (Ears - Whisper via audio_handler)
    user_text = transcribe()
    
    if user_text:
        print(f"\n[USER]: {user_text}")
        
        # Step 2: Think (Brain - Llama 3.1)
        jarvis_response = think_and_respond(user_text)
        
        # Step 3: Respond (Text output for now, TTS will be next)
        print(f"\n[JARVIS]: {jarvis_response}")
    else:
        print("\n[SYSTEM]: No audio detected.")

if __name__ == "__main__":
    run_jarvis()