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
        print("ERROR: Ошибка: Клиент ИИ не инициализирован. Проверьте GEMINI_API_KEY в .env")
        return

    print("OK: Клиент ИИ инициализирован.")
    
    user_id = 12345
    user_text = "Привет! Как тебя зовут и какую модель ты используешь?"
    history = []
    
    # Создаем тестовый профиль
    mock_profile = {
        "profile_summary": {
            "summary": "Разработчик Python, интересуется ИИ.",
            "interests": ["Coding", "AI", "Startup"],
            "new_skills": ["Python", "FastAPI"],
            "pain_points": ["Lack of time"],
            "dreams": ["Create UPA", "Learn identifying birds"]
        }
    }
    user_name = "TestUser"

    print(f"Запрос к ИИ: '{user_text}'...")
    print(f"С профилем: {mock_profile['profile_summary']['summary']}")
    
    try:
        response = await engine.generate_response(
            user_id=user_id, 
            user_text=user_text, 
            history=history,
            user_profile=mock_profile,
            user_name=user_name
        )
        print("\n--- Ответ ИИ ---")
        print(response)
        print("----------------\n")
        
        if "ошибка" in response.lower() or "извините" in response.lower():
             print("WARNING: Возможно, произошла ошибка или модель вернула шаблонный отказ.")
        else:
             print("SUCCESS: Ответ получен успешно!")
             
    except Exception as e:
        print(f"ERROR: Произошла ошибка при тестировании: {e}")

if __name__ == "__main__":
    asyncio.run(test_ai_response())
