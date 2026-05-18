# Code Doc Agent

Веб-прототип агентной системы для автодокументирования Python-кода.

Пользователь задаёт вопрос по проекту, а LLM через инструменты ищет нужные функции, получает их код и формирует описание. Диалог можно продолжать: сначала попросить план, затем уточнить его и после этого запросить финальный ответ.

## Как работает

```text
вопрос пользователя
→ LLM решает, нужна ли информация из кода
→ при необходимости вызывает search_symbols
→ программа ищет функции/классы в индексе
→ LLM вызывает get_symbol_details
→ программа возвращает тело функции
→ LLM формирует ответ
→ пользователь может задать уточнение в том же чате
```

LLM не читает весь проект сразу. Она работает через индекс и инструменты.

## Инструменты агента

- `search_symbols` — ищет функции, методы и классы по индексу проекта.
- `get_symbol_details` — возвращает подробности по найденному элементу: путь, сигнатуру, строки, docstring и тело кода.

System prompt вынесен в отдельный файл:

```text
prompts/system_prompt.txt
```

## Структура проекта

```text
main.py                    # CLI-запуск агента
web_app.py                 # веб-приложение FastAPI
requirements.txt           # зависимости
.env.example               # пример настроек
prompts/system_prompt.txt  # системный промпт агента

code_agent/
  agent.py                 # агентный цикл
  config.py                # загрузка .env
  conversation_store.py    # хранение истории диалогов
  indexer.py               # индексатор Python-кода
  index_store.py           # поиск и чтение символов из индекса
  llm_client.py            # OpenAI-compatible клиент
  logger.py                # JSONL-логирование
  tools.py                 # инструменты агента

static/
  index.html               # интерфейс чата
  app.js                   # логика чата и streaming
  style.css                # стили

example_project/
  app/security/access.py   # тестовый проект
```

## Установка

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Если PowerShell запрещает активацию окружения:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Настройка `.env`

Скопировать пример настроек:

```bash
cp .env.example .env
```

На Windows:

```powershell
copy .env.example .env
```

Открыть `.env` и указать токен:

```env
LLM_API_KEY=hf_твой_токен
LLM_BASE_URL=https://router.huggingface.co/v1
LLM_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
LLM_TEMPERATURE=0.1

AGENT_INDEX_PATH=code_index.json
AGENT_LOG_PATH=logs/agent_steps.jsonl
AGENT_MAX_STEPS=8
AGENT_SYSTEM_PROMPT_PATH=prompts/system_prompt.txt

WEB_CONVERSATION_DIR=data/conversations
```

Файл `.env` нельзя добавлять в Git.

## Построение индекса

Для тестового проекта:

```bash
python -m code_agent.indexer example_project --output code_index.json
```

Для своего проекта:

```bash
python -m code_agent.indexer /path/to/project --output code_index.json
```

После этого появится файл:

```text
code_index.json
```

В нём хранится информация о функциях, методах и классах проекта.

## Запуск веб-чата

```bash
uvicorn web_app:app --reload
```

Открыть в браузере:

```text
http://127.0.0.1:8000
```

В чате можно писать, например:

```text
Составь план ответа: как проверяется доступ пользователя
```

Потом можно уточнить:

```text
Раздел про функции распиши подробнее, а про класс User кратко
```

И затем запросить финальный ответ:

```text
Теперь сгенерируй финальное описание по этому плану
```

История диалогов сохраняется в:

```text
data/conversations/
```

Диалоги можно удалять из боковой панели. При удалении удаляется соответствующий JSON-файл истории на сервере.

## CLI-запуск

Старый режим через командную строку тоже оставлен:

```bash
python main.py "Опиши, как проверяется доступ пользователя"
```

С явным указанием индекса:

```bash
python main.py "Опиши, как проверяется доступ пользователя" --index code_index.json
```

С другим system prompt:

```bash
python main.py "Опиши, как проверяется доступ пользователя" --prompt prompts/system_prompt.txt
```

## Логи

Логи работы агента сохраняются в:

```text
logs/agent_steps.jsonl
```

В них видно:

- какой ответ дала LLM;
- какой инструмент она вызвала;
- какие аргументы передала;
- что вернул инструмент.

## Пример работы

Вопрос:

```text
Опиши, как проверяется доступ пользователя
```

Агент может выполнить цепочку:

```text
1. search_symbols("access permission check")
2. найдено require_execute_access
3. get_symbol_details("app.security.access.require_execute_access")
4. найден вызов get_user_permissions
5. get_symbol_details("app.security.access.get_user_permissions")
6. сформирован итоговый ответ
```

Важно: имена функций не придумываются моделью. Они берутся из индекса реального проекта.

## Лимит вызовов инструментов в чате

Лимит `AGENT_MAX_STEPS` применяется к одному пользовательскому сообщению.

Когда пользователь отправляет новое сообщение в веб-чате, запускается новый агентный цикл: счётчик шагов, список уже выполненных tool calls и счётчик пустых поисков создаются заново. История диалога при этом сохраняется и передаётся в LLM как контекст.

Это позволяет продолжать диалог: сначала запросить план ответа, потом уточнить его, а затем попросить сгенерировать финальный текст. При каждом новом сообщении агент снова может использовать инструменты в пределах нового лимита.


## Структурированный JSON

В веб-интерфейсе есть кнопка **JSON по схеме**.

Она используется после того, как агент уже ответил на вопрос по коду. Например:

```text
Опиши, как проверяется доступ пользователя
```

После получения текстового ответа можно нажать **JSON по схеме**. Тогда система:

```text
1. Берёт историю текущего диалога.
2. Берёт сохранённые результаты инструментов из текущего диалога.
3. Передаёт LLM JSON Schema и фактические данные из get_symbol_details.
4. Просит LLM сформировать JSON по заданной схеме.
5. Валидирует ответ через Pydantic.
6. Если JSON некорректный, отправляет ошибку валидации обратно LLM.
7. Получает исправленный JSON и снова проверяет его.
8. Дополнительно дозаполняет точные поля из результатов инструментов.
```


Теперь JSON формируется не только по тексту последнего ответа, но и по фактическим данным инструментов. Поэтому поля `qualified_name`, `file_path`, имена параметров и связанные функции берутся из результатов `search_symbols` и `get_symbol_details`.

Схема описана в файле:

```text
code_agent/schemas.py
```

Основная структура:

```json
{
  "exact_method_name": "require_execute_access",
  "qualified_name": "app.security.access.require_execute_access",
  "file_path": "app/security/access.py",
  "description": "Описание логики метода",
  "parameters": [
    {
      "name": "user",
      "class_name": "User",
      "description": "Пользователь, для которого проверяется доступ",
      "properties": [
        {
          "name": "role",
          "class_name": "str",
          "description": "Роль пользователя"
        }
      ]
    }
  ],
  "related_symbols": [
    {
      "name": "get_user_permissions",
      "relation": "calls",
      "description": "Функция вызывается для получения списка прав"
    }
  ]
}
```

Лог генерации и исправления JSON записывается в тот же файл:

```text
logs/agent_steps.jsonl
```

Там можно увидеть события:

```text
json_generation_response
json_generation_invalid
json_generation_valid
```
