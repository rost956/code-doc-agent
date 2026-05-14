# Code ReAct Agent MVP

Минимальный агент для автодокументирования Python-кода.

## Как работает

```text
1. вопрос пользователя
2. LLM выбирает, что искать
3. вызывается инструмент search_symbols
4. находится нужная функция/класс
5. вызывается get_symbol_details
6. LLM получает код
7. формируется итоговое описание
```

## Инструменты

- `search_symbols` — ищет функции, методы и классы по индексу проекта.
- `get_symbol_details` — возвращает подробности по найденному элементу: сигнатуру, путь, строки и тело кода.

## Структура проекта

```text
main.py                 # запуск агента
requirements.txt        # зависимости
.env.example            # пример настроек
.gitignore

code_agent/
  agent.py              # агентный цикл
  config.py             # загрузка .env
  indexer.py            # индексатор Python-кода
  index_store.py        # поиск по индексу
  llm_client.py         # OpenAI-compatible клиент
  logger.py             # логирование
  tools.py              # инструменты агента

example_project/
  app/security/access.py # тестовый проект
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

## Настройка

В `.env` указать токен и модель:

```env
LLM_API_KEY=hf_токен
LLM_BASE_URL=https://router.huggingface.co/v1
LLM_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
LLM_TEMPERATURE=0.1

AGENT_INDEX_PATH=code_index.json
AGENT_LOG_PATH=logs/agent_steps.jsonl
AGENT_MAX_STEPS=8
```

## Построение индекса

Для тестового проекта:

```bash
python -m code_agent.indexer example_project --output code_index.json
```

Для своего проекта:

```bash
python -m code_agent.indexer /path/to/project --output code_index.json
```

## Запуск

```bash
python main.py "Опиши, как проверяется доступ пользователя"
```

С явным указанием индекса:

```bash
python main.py "Опиши, как проверяется доступ пользователя" --index code_index.json
```

С ограничением числа шагов:

```bash
python main.py "Опиши, как проверяется доступ пользователя" --max-steps 6
```

## Логи

Логи сохраняются в файл:

```text
logs/agent_steps.jsonl
```

В них видно:

- какие инструменты вызывала LLM;
- какие аргументы передавала;
- какие результаты возвращали инструменты;
- на каком шаге был сформирован ответ.

## Пример

Вопрос:

```text
Опиши, как проверяется доступ пользователя
```

Агент может выполнить такую цепочку:

```text
1. search_symbols("access permission check")
2. найдено require_execute_access
3. get_symbol_details("app.security.access.require_execute_access")
4. найден вызов get_user_permissions
5. get_symbol_details("app.security.access.get_user_permissions")
6. формируется итоговое описание
```