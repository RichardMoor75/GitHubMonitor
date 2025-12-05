# -*- coding: utf-8 -*-
"""
github_monitor_openrouter.py - GitHub Monitor с OpenRouter API

Агент для мониторинга GitHub репозиториев с AI-саммаризацией через OpenRouter API
и отправки уведомлений в Telegram о новых версиях.

Преимущества OpenRouter:
- Единый API для 400+ моделей
- OpenAI-совместимый интерфейс
- Легкое переключение между моделями
- Те же цены что напрямую у провайдеров

Использует GPT-4o-mini через OpenRouter: $0.15/1M input + $0.60/1M output

Зависимости: pip install httpx openai python-dotenv tenacity
"""

import asyncio
import logging
import json
import os
import re
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime
from logging.handlers import RotatingFileHandler

import httpx
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, AsyncRetrying
from dotenv import load_dotenv

# --- Загрузка переменных окружения ---
load_dotenv()

# --- Константы ---
BASE_DIR = Path(__file__).parent
STATE_FILE_NAME = "github_releases_state.json"
REPOS_FILE_NAME = "repos_to_monitor.json"
STATE_PATH = BASE_DIR / STATE_FILE_NAME
REPOS_FILE_PATH = BASE_DIR / REPOS_FILE_NAME
LOG_FILE_PATH = BASE_DIR / "github_monitor.log"

# Telegram лимиты
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# OpenRouter настройки
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- Настройка логирования ---
# Ротация логов: макс 5 МБ, хранить последние 5 файлов
rotating_handler = RotatingFileHandler(
    LOG_FILE_PATH, 
    maxBytes=5*1024*1024, 
    backupCount=5, 
    encoding='utf-8'
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        rotating_handler,
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("GitHubMonitorOpenRouter")

# --- Загрузка конфигурации ---
def load_configuration() -> Tuple[str, int, str, Optional[str], str, str]:
    """Загружает конфигурацию исключительно из переменных окружения (.env)."""
    try:
        bot_token = os.getenv('MONITOR_BOT_TOKEN')
        admin_chat_id_str = os.getenv('MONITOR_ADMIN_CHAT_ID')
        openrouter_api_key = os.getenv('OPENROUTER_API_KEY')
        github_token = os.getenv('GITHUB_TOKEN')
        openrouter_model = os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
        summary_language = os.getenv('SUMMARY_LANGUAGE', 'русском языке') # Default to Russian
        
        # Валидация обязательных полей
        if not bot_token:
            raise ValueError("Не задан MONITOR_BOT_TOKEN в .env")
        
        if not admin_chat_id_str:
            raise ValueError("Не задан MONITOR_ADMIN_CHAT_ID в .env")
            
        try:
            admin_chat_id = int(admin_chat_id_str)
        except ValueError:
            raise ValueError(f"MONITOR_ADMIN_CHAT_ID должен быть числом, получено: {admin_chat_id_str}")

        if not openrouter_api_key or 'ВСТАВЬ_СЮДА' in openrouter_api_key:
            raise ValueError("API-ключ для OpenRouter не настроен. Получите ключ на https://openrouter.ai/")
        
        return bot_token, admin_chat_id, openrouter_api_key, github_token, openrouter_model, summary_language
        
    except Exception as e:
        logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА при загрузке конфигурации: {e}")
        raise

# Глобальные переменные для конфигурации
BOT_TOKEN, ADMIN_CHAT_ID, OPENROUTER_API_KEY, GITHUB_TOKEN, OPENROUTER_MODEL, SUMMARY_LANGUAGE = load_configuration()

# Инициализация OpenRouter клиента
# OpenRouter использует OpenAI-совместимый API
openrouter_client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL
)

# --- Загрузка репозиториев ---
def load_repos_to_monitor() -> Dict[str, str]:
    """Загружает и валидирует список репозиториев из JSON."""
    if not REPOS_FILE_PATH.exists():
        logger.critical(f"Файл {REPOS_FILE_NAME} не найден")
        return {}
    
    try:
        with open(REPOS_FILE_PATH, 'r', encoding='utf-8') as f:
            repos = json.load(f)
        
        if not isinstance(repos, dict):
            raise ValueError("JSON должен содержать объект")
        
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in repos.items()):
            raise ValueError("Все ключи и значения должны быть строками")
        
        logger.info(f"✅ Загружено {len(repos)} репозиториев")
        return repos
        
    except json.JSONDecodeError as e:
        logger.error(f"Невалидный JSON в {REPOS_FILE_NAME}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Ошибка при загрузке репозиториев: {e}")
        return {}

# --- Управление состоянием ---
def load_state() -> Dict[str, int]:
    """Загружает состояние последних проверенных релизов."""
    if not STATE_PATH.exists():
        logger.info(f"Файл состояния не найден, создаю новый")
        return {}
    
    try:
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.warning("⚠️ Файл состояния поврежден, начинаю с чистого листа")
        return {}
    except Exception as e:
        logger.error(f"Ошибка при загрузке состояния: {e}")
        return {}

def save_state(state: Dict[str, int]):
    """Сохраняет состояние в файл."""
    try:
        with open(STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        logger.debug("Состояние успешно сохранено")
    except IOError as e:
        logger.error(f"❌ Не удалось сохранить состояние: {e}")

# --- Безопасное экранирование Markdown V2 ---
def convert_ai_markdown_to_telegram(text: str) -> str:
    """
    Конвертирует AI markdown в Telegram MarkdownV2 с агрессивным экранированием.
    
    Telegram MarkdownV2 требует экранирования символов:
    _ * [ ] ( ) ~ ` > # + - = | { } . !
    
    Алгоритм:
    1. Вырезаем и сохраняем ссылки [text](url)
    2. Вырезаем и сохраняем жирный текст **text** (превращая в *text*)
    3. Вырезаем и сохраняем списки (строки, начинающиеся с • или -)
    4. Экранируем ВСЕ остальные спецсимволы в оставшемся тексте
    5. Возвращаем сохраненные блоки на места
    """
    if not text:
        return ""

    # 1. Сохраняем ссылки
    links = []
    def save_link(match):
        placeholder = f"LINK_PH_{len(links)}"
        links.append(match.group(0))
        return placeholder
    
    # Сначала ссылки, чтобы внутри них не испортить ничего
    text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', save_link, text)

    # 2. Сохраняем жирный текст **bold** -> *bold*
    bolds = []
    def save_bold(match):
        placeholder = f"BOLD_PH_{len(bolds)}"
        # Telegram использует * для жирного, AI использует **
        content = match.group(1)
        bolds.append(f"*{content}*") 
        return placeholder
    
    text = re.sub(r'\*\*([^\*]+)\*\*', save_bold, text)

    # 3. Сохраняем код `code`
    codes = []
    def save_code(match):
        placeholder = f"CODE_PH_{len(codes)}"
        codes.append(match.group(0))
        return placeholder
        
    text = re.sub(r'`([^`]+)`', save_code, text)

    # 4. Экранирование всех спецсимволов MarkdownV2
    # Список: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    escaped_text = ""
    
    for char in text:
        if char in escape_chars:
            escaped_text += f"\\{char}"
        else:
            escaped_text += char
            
    text = escaped_text

    # 5. Восстанавливаем сохраненные блоки (в обратном порядке вложенности, если бы она была)
    
    # Восстанавливаем код (он уже экранирован внутри себя не должен быть, но markdown v2 требует экранирования ` внутри `...`? Нет, внутри `...` экранирование работает иначе, но мы просто вернем как есть)
    for i, code in enumerate(codes):
        text = text.replace(f"CODE_PH_{i}", code)

    # Восстанавливаем жирный текст
    for i, bold in enumerate(bolds):
        text = text.replace(f"BOLD_PH_{i}", bold)
        
    # Восстанавливаем ссылки
    for i, link in enumerate(links):
        text = text.replace(f"LINK_PH_{i}", link)

    return text

def escape_markdown_v2(text: str) -> str:
    """Экранирует спецсимволы Telegram MarkdownV2, сохраняя ссылки."""
    if not text:
        return ""
    
    links = []
    
    def link_replacer(match):
        placeholder = f"__LINK_{uuid.uuid4().hex}__"
        links.append((placeholder, match.group(0)))
        return placeholder
    
    text_without_links = re.sub(r'\[.*?\]\(.*?\)', link_replacer, text)
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    escaped_text = re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text_without_links)
    
    for placeholder, original_link in links:
        escaped_text = escaped_text.replace(placeholder, original_link)
    
    return escaped_text

# --- Чанкование сообщений для Telegram ---
def split_message_markdown(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> List[str]:
    """
    Разбивает длинное сообщение на части с учетом MarkdownV2 синтаксиса.
    
    Гарантирует что:
    - Каждая часть не превышает max_length
    - Markdown-форматирование остается корректным
    - Ссылки не разрываются
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    current_chunk = ""
    
    # Разбиваем по параграфам
    paragraphs = text.split('\n\n')
    
    for paragraph in paragraphs:
        # Если добавление параграфа не превысит лимит
        if len(current_chunk) + len(paragraph) + 2 <= max_length:
            if current_chunk:
                current_chunk += '\n\n'
            current_chunk += paragraph
        else:
            # Если текущий чанк не пустой - сохраняем его
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = paragraph
            else:
                # Если параграф сам по себе слишком длинный - разбиваем по строкам
                lines = paragraph.split('\n')
                for line in lines:
                    if len(current_chunk) + len(line) + 1 <= max_length:
                        if current_chunk:
                            current_chunk += '\n'
                        current_chunk += line
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = line
    
    # Добавляем последний чанк
    if current_chunk:
        chunks.append(current_chunk)
    
    # Добавляем индикаторы частей если сообщение разбито
    if len(chunks) > 1:
        for i, chunk in enumerate(chunks, 1):
            chunks[i-1] = f"{chunk}\n\n_{escape_markdown_v2(f'(часть {i}/{len(chunks)})')}_"
    
    return chunks

# --- AI-саммаризация через OpenRouter ---
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((Exception,)),
    reraise=True
)
def get_openrouter_summary_with_retry(release_notes: str, language: str) -> str:
    """
    Получает AI-саммари через OpenRouter с автоматическими повторными попытками.
    
    Использует GPT-4o-mini через OpenRouter: $0.15/1M input + $0.60/1M output
    """
    if not release_notes or release_notes == 'Нет описания.':
        return 'Нет описания.'
    
    # Обрезаем слишком длинные release notes для экономии токенов
    max_length = 4000  # символов (увеличено для более развернутых ответов)
    if len(release_notes) > max_length:
        release_notes = release_notes[:max_length] + "\n\n... (текст обрезан)"
        logger.info(f"📝 Release notes обрезаны до {max_length} символов")
    
    # Формируем структурированный запрос в виде JSON
    # Это помогает модели четко отделить инструкции от контента и строго следовать языковым настройкам
    prompt_structure = {
        "task": (
            "Perform a deep analysis of the release notes and generate a COMPREHENSIVE and DETAILED summary "
            "for system administrators. Your goal is NOT just to list changes, but to EXPLAIN their practical impact."
        ),
        "target_language": language,
        "formatting_rules": {
            "format": "Markdown",
            "verbosity": "Verbose and explanatory. Avoid brevity. Expand on 'why' a change matters.",
            "headers": "Use **double asterisks** for headers (e.g. **New Features**)",
            "lists": "Use • for list items",
            "emojis": "Use 🔒 for security, ⚡ for performance, ⚠️ for breaking changes",
            "forbidden": "NO technical tags, NO metadata, NO code blocks unless necessary",
            "structure": [
                "**New Features** (List each feature, then hyphen, then a DETAILED explanation of what it does and why it is useful)",
                "**Fixes** (Explain the bug and the resolution)",
                "**Improvements** (Explain the optimization and its benefit)",
                "**Breaking Changes** (Detailed migration steps if needed)"
            ]
        },
        "source_text": release_notes
    }
    
    # Сериализуем в строку для отправки
    user_content = json.dumps(prompt_structure, ensure_ascii=False)

    try:
        logger.info(f"🤖 Запрашиваю OpenRouter API (модель: {OPENROUTER_MODEL})...")
        
        # Вызов OpenRouter API
        response = openrouter_client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert Senior DevOps Engineer and System Administrator. "
                        "You excel at explaining technical changes to humans. "
                        "You will receive a JSON object with source text. "
                        "Analyze it deeply. If the release notes are brief, use your expert knowledge to infer the context "
                        "and importance of the changes (without hallucinating non-existent features). "
                        "Output strictly in the 'target_language'. "
                        "Output clean, formatted Markdown."
                    )
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            max_tokens=1000,
            temperature=0.3,
            extra_headers={
                "HTTP-Referer": "https://github.com/your-username/github-monitor",
                "X-Title": "GitHub Release Monitor"
            }
        )
        
        # Извлекаем текст ответа
        summary = response.choices[0].message.content.strip()
        
        if summary and len(summary) > 10:
            total_tokens = response.usage.total_tokens if hasattr(response, 'usage') else 0
            logger.info(f"✅ AI-саммари получено от OpenRouter (токенов: {total_tokens})")
            # Используем специальную конвертацию markdown для AI ответов
            return convert_ai_markdown_to_telegram(summary)
        else:
            raise ValueError("Пустой ответ от OpenRouter")
            
    except Exception as e:
        logger.error(f"❌ Ошибка OpenRouter API: {e}")
        raise

def get_openrouter_summary(release_notes: str, language: str) -> str:
    """Обертка для OpenRouter-саммаризации с fallback на упрощенный текст."""
    try:
        return get_openrouter_summary_with_retry(release_notes, language)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка OpenRouter после всех попыток: {e}")
        logger.warning("📝 Возвращаю упрощенный оригинальный текст")
        
        # Fallback: очистка и экранирование оригинального текста
        plain_text = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1', release_notes)
        plain_text = re.sub(r'[*_`~#>]', '', plain_text)
        plain_text = re.sub(r'\n\s*\n+', '\n', plain_text).strip()
        
        if len(plain_text) > 500:
            plain_text = plain_text[:497] + "..."
        
        return escape_markdown_v2(plain_text)

# --- Отправка в Telegram ---
async def send_telegram_message(message: str):
    """
    Асинхронная отправка сообщения в Telegram с автоматическим чанкованием.
    
    Если сообщение превышает лимит Telegram (4096 символов), 
    оно автоматически разбивается на несколько частей.
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    # Разбиваем длинное сообщение на части
    message_chunks = split_message_markdown(message)
    
    if len(message_chunks) > 1:
        logger.info(f"📨 Сообщение разбито на {len(message_chunks)} частей")
    
    try:
        async with httpx.AsyncClient() as client:
            for i, chunk in enumerate(message_chunks, 1):
                payload = {
                    'chat_id': ADMIN_CHAT_ID,
                    'text': chunk,
                    'parse_mode': 'MarkdownV2',
                    'disable_web_page_preview': True
                }
                
                response = await client.post(url, data=payload, timeout=20)
                response.raise_for_status()
                
                if len(message_chunks) > 1:
                    logger.info(f"✉️ Часть {i}/{len(message_chunks)} отправлена в Telegram")
                    # Небольшая задержка между сообщениями
                    await asyncio.sleep(0.5)
                else:
                    logger.info("✉️ Уведомление отправлено в Telegram")
            
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ Telegram API ошибка: {e.response.text}")
        raise
    except httpx.RequestError as e:
        logger.error(f"❌ Сетевая ошибка Telegram: {e}")
        raise

async def send_error_notification(error_msg: str):
    """Отправляет уведомление об ошибке администратору."""
    try:
        message = (
            f"⚠️ *Ошибка мониторинга GitHub*\n\n"
            f"`{escape_markdown_v2(error_msg)}`\n\n"
            f"Время: {escape_markdown_v2(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}"
        )
        await send_telegram_message(message)
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление об ошибке: {e}")

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, AsyncRetrying

# ... (imports remain the same)

# --- Проверка репозитория ---
async def check_repo_for_updates(
    client: httpx.AsyncClient,
    repo_name: str,
    repo_path: str,
    last_seen_id: Optional[int]
) -> Optional[int]:
    """Асинхронная проверка репозитория на наличие новых релизов."""
    try:
        url = f"https://api.github.com/repos/{repo_path}/releases/latest"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHub-Monitor-OpenRouter/1.0"
        }
        
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
            logger.debug(f"🔑 Используется GitHub token для {repo_name}")
        
        # Попытка запроса с повторами (Retries)
        # Пытаемся 3 раза с экспоненциальной задержкой при сетевых ошибках
        try:
            async for attempt in AsyncRetrying(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=10), reraise=True):
                with attempt:
                    response = await client.get(url, headers=headers, timeout=15)
                    # Если 5xx ошибка сервера - рейзим, чтобы сработал retry
                    if response.status_code >= 500:
                        response.raise_for_status()
                    # Если 4xx (кроме 404, 403) - это ошибки клиента, retry не поможет, но обработаем ниже
        except httpx.HTTPStatusError as e:
            # Пробрасываем дальше для обработки кодов 404/403
            response = e.response 
            if response.status_code < 500:
                pass # Это не серверная ошибка, идем дальше к raise_for_status()
            else:
                raise # Серверная ошибка после всех попыток
        except Exception as e:
            # Сетевые ошибки после всех попыток
            logger.error(f"[{repo_name}] ❌ Сетевая ошибка после 3 попыток: {e}")
            raise e

        response.raise_for_status()
        
        latest_release = response.json()
        release_id = latest_release['id']
        
        if release_id == last_seen_id:
            logger.info(f"[{repo_name}] ✔️ Нет обновлений ({latest_release['tag_name']})")
            return None
        
        logger.info(f"[{repo_name}] 🔥 НОВЫЙ РЕЛИЗ: {latest_release['tag_name']}")
        
        tag_name = latest_release['tag_name']
        html_url = latest_release['html_url']
        published_at = latest_release.get('published_at', 'неизвестно')
        is_prerelease = latest_release.get('prerelease', False)
        original_body = latest_release.get('body') or 'Нет описания.'
        
        logger.info(f"[{repo_name}] 🤖 Запрашиваю OpenRouter AI-саммари...")
        # OpenRouter API работает синхронно, используем asyncio.to_thread
        openrouter_summary = await asyncio.to_thread(get_openrouter_summary, original_body, SUMMARY_LANGUAGE)
        
        prerelease_tag = "🧪 PRE\\-RELEASE" if is_prerelease else ""
        
        # Улучшенное форматирование сообщения
        message = (
            f"🎉 *New Release: {escape_markdown_v2(repo_name)}*\n"
            f"📦 Version: `{escape_markdown_v2(tag_name)}` {prerelease_tag}\n"
            f"📅 Date: {escape_markdown_v2(published_at[:10])}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"{openrouter_summary}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"[📖 Full changelog]({html_url})"
        )
        
        await send_telegram_message(message)
        return release_id
        
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # Проверяем, существует ли сам репозиторий (возможно просто нет релизов)
            try:
                repo_check_url = f"https://api.github.com/repos/{repo_path}"
                repo_resp = await client.head(repo_check_url, headers=headers, timeout=10)
                if repo_resp.status_code == 200:
                    logger.info(f"[{repo_name}] ℹ️ Репозиторий доступен, но релизов пока нет")
                    return None
            except Exception as check_e:
                logger.warning(f"[{repo_name}] Не удалось проверить наличие репозитория: {check_e}")

            logger.error(f"[{repo_name}] ❌ Репозиторий не найден: {repo_path}")
            await send_error_notification(f"Репозиторий {repo_name} ({repo_path}) не найден")
        elif e.response.status_code == 403:
            logger.warning(f"[{repo_name}] ⚠️ Rate limit достигнут")
        else:
            logger.error(f"[{repo_name}] ❌ GitHub API ошибка {e.response.status_code}")
            
    except Exception as e:
        logger.error(f"[{repo_name}] ❌ Неизвестная ошибка: {e}", exc_info=True)
        await send_error_notification(f"Ошибка при проверке {repo_name}: {str(e)}")
    
    return None

# --- Главная функция ---
async def main():
    """Асинхронная главная функция мониторинга."""
    logger.info("=" * 60)
    logger.info("🚀 Запуск GitHub Monitor (OpenRouter Edition)")
    logger.info(f"⏰ Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"🤖 AI: OpenRouter → {OPENROUTER_MODEL}")
    logger.info(f"💰 Цена: $0.15/1M input + $0.60/1M output")
    logger.info("=" * 60)
    
    repos_to_monitor = load_repos_to_monitor()
    if not repos_to_monitor:
        logger.critical("❌ Нет репозиториев для мониторинга. Выход.")
        return
    
    current_state = load_state()
    new_state = current_state.copy()
    
    async with httpx.AsyncClient() as client:
        tasks = []
        
        for repo_name, repo_path in repos_to_monitor.items():
            last_id = current_state.get(repo_name)
            task = check_repo_for_updates(client, repo_name, repo_path, last_id)
            tasks.append((repo_name, task))
        
        results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)
        
        for (repo_name, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                logger.error(f"[{repo_name}] Исключение: {result}")
            elif result:
                new_state[repo_name] = result
                logger.info(f"[{repo_name}] ✅ Состояние обновлено")
    
    save_state(new_state)
    
    logger.info("=" * 60)
    logger.info("✅ Проверка завершена успешно")
    logger.info("=" * 60)

# --- Точка входа ---
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⚠️ Прервано пользователем")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)
        exit(1)