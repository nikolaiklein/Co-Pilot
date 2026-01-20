import asyncio
import os
from dotenv import load_dotenv
from services.ai_engine import AIEngine

# Загружаем переменные окружения
load_dotenv()

async def test_ai_response():
    print("--- Тестирование Gemini 3 Pro Preview ---")
    
    # Инициализируем AI Engine
    engine = AIEngine()
    
    if not engine.client:
        print("❌ Ошибка: Клиент ИИ не инициализирован. Проверьте GEMINI_API_KEY в .env")
        return

    print("✅ Клиент ИИ инициализирован.")
    
    user_id = 12345
    user_text = "Привет! Как тебя зовут и какую модель ты используешь?"
    history = []
    
    print(f"Запрос к ИИ: '{user_text}'...")
    
    try:
        response = await engine.generate_response(user_id, user_text, history)
        print("\n--- Ответ ИИ ---")
        print(response)
        print("----------------\n")
        
        if "ошибка" in response.lower() or "извините" in response.lower():
             print("⚠️ Возможно, произошла ошибка или модель вернула шаблонный отказ.")
        else:
             print("✅ Ответ получен успешно!")
             
    except Exception as e:
        print(f"❌ Произошла ошибка при тестировании: {e}")

if __name__ == "__main__":
    asyncio.run(test_ai_response())
