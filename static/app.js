const state = {
  conversationId: localStorage.getItem('conversationId') || null,
  isStreaming: false,
};

const messagesEl = document.getElementById('messages');
const stepsEl = document.getElementById('steps');
const stepsBodyEl = document.getElementById('stepsBody');
const formEl = document.getElementById('chatForm');
const inputEl = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const planBtn = document.getElementById('planBtn');
const jsonBtn = document.getElementById('jsonBtn');
const newChatBtn = document.getElementById('newChatBtn');
const conversationListEl = document.getElementById('conversationList');
const chatTitleEl = document.getElementById('chatTitle');

init();

async function init() {
  await loadConversations();
  if (state.conversationId) {
    await loadConversation(state.conversationId);
  } else {
    await createConversation();
  }
}

newChatBtn.addEventListener('click', async () => {
  await createConversation();
});

planBtn.addEventListener('click', () => {
  inputEl.value = 'Составь план ответа по этому вопросу. Пока не пиши финальный ответ, только перечисли разделы и укажи, где нужна детализация.';
  inputEl.focus();
});

jsonBtn.addEventListener('click', async () => {
  if (state.isStreaming || !state.conversationId) return;
  await generateStructuredJson();
});

formEl.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = inputEl.value.trim();
  if (!message || state.isStreaming) return;
  inputEl.value = '';
  await sendMessage(message);
});

async function createConversation() {
  const response = await fetch('/api/conversations', { method: 'POST' });
  const conversation = await response.json();
  state.conversationId = conversation.id;
  localStorage.setItem('conversationId', conversation.id);
  renderConversation(conversation);
  await loadConversations();
}

async function loadConversations() {
  const response = await fetch('/api/conversations');
  const conversations = await response.json();
  conversationListEl.innerHTML = '';

  for (const item of conversations) {
    const row = document.createElement('div');
    row.className = 'conversation-row' + (item.id === state.conversationId ? ' active' : '');

    const openBtn = document.createElement('button');
    openBtn.className = 'conversation-item';
    openBtn.innerHTML = `${escapeHtml(item.title || 'Без названия')}<small>${item.message_count || 0} сообщений</small>`;
    openBtn.addEventListener('click', () => loadConversation(item.id));

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'delete-conversation';
    deleteBtn.type = 'button';
    deleteBtn.title = 'Удалить диалог';
    deleteBtn.textContent = '×';
    deleteBtn.addEventListener('click', async (event) => {
      event.stopPropagation();
      await deleteConversation(item.id);
    });

    row.appendChild(openBtn);
    row.appendChild(deleteBtn);
    conversationListEl.appendChild(row);
  }
}

async function loadConversation(conversationId) {
  const response = await fetch(`/api/conversations/${conversationId}`);
  if (!response.ok) {
    await createConversation();
    return;
  }
  const conversation = await response.json();
  state.conversationId = conversation.id;
  localStorage.setItem('conversationId', conversation.id);
  renderConversation(conversation);
  await loadConversations();
}

async function deleteConversation(conversationId) {
  if (state.isStreaming) return;

  const accepted = confirm('Удалить этот диалог?');
  if (!accepted) return;

  const response = await fetch(`/api/conversations/${conversationId}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    alert('Не удалось удалить диалог');
    return;
  }

  const wasActive = conversationId === state.conversationId;

  const listResponse = await fetch('/api/conversations');
  const conversations = await listResponse.json();

  if (wasActive) {
    localStorage.removeItem('conversationId');
    state.conversationId = null;

    if (conversations.length > 0) {
      await loadConversation(conversations[0].id);
    } else {
      await createConversation();
    }
    return;
  }

  await loadConversations();
}

function renderConversation(conversation) {
  messagesEl.innerHTML = '';
  stepsBodyEl.innerHTML = '';
  stepsEl.classList.add('hidden');
  chatTitleEl.textContent = conversation.title || 'Новый диалог';
  for (const message of conversation.messages || []) {
    addMessage(message.role, message.content);
  }
  scrollMessages();
}

async function sendMessage(message) {
  state.isStreaming = true;
  sendBtn.disabled = true;
  planBtn.disabled = true;
  jsonBtn.disabled = true;

  addMessage('user', message);
  const assistantMessage = addMessage('assistant', '');
  stepsBodyEl.innerHTML = '';
  stepsEl.classList.remove('hidden');

  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: state.conversationId,
        message,
      }),
    });

    if (!response.ok || !response.body) {
      throw new Error(`HTTP ${response.status}`);
    }

    await readEventStream(response.body, (eventName, data) => {
      if (eventName === 'conversation') {
        state.conversationId = data.conversation_id;
        localStorage.setItem('conversationId', data.conversation_id);
      }

      if (eventName === 'status') {
        addStep(`LLM: ${data.message}`);
      }

      if (eventName === 'tool_call') {
        addStep(`TOOL CALL: ${data.tool_name}(${data.arguments})`);
      }

      if (eventName === 'tool_result') {
        const preview = JSON.stringify(data.result, null, 2);
        addStep(`TOOL RESULT: ${data.tool_name}\n${preview.slice(0, 1200)}`);
      }

      if (eventName === 'final_delta') {
        assistantMessage.textContent += data.delta;
        scrollMessages();
      }

      if (eventName === 'error') {
        assistantMessage.textContent += `\nОшибка: ${data.message}`;
      }
    });
  } catch (error) {
    assistantMessage.textContent = `Ошибка запроса: ${error.message}`;
  } finally {
    state.isStreaming = false;
    sendBtn.disabled = false;
    planBtn.disabled = false;
    jsonBtn.disabled = false;
    await loadConversations();
  }
}


async function generateStructuredJson() {
  state.isStreaming = true;
  sendBtn.disabled = true;
  planBtn.disabled = true;
  jsonBtn.disabled = true;

  const assistantMessage = addMessage('assistant', 'Формирую JSON по схеме и проверяю структуру...');

  try {
    const response = await fetch(`/api/conversations/${state.conversationId}/structured-json`, {
      method: 'POST',
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || `HTTP ${response.status}`);
    }

    if (data.ok) {
      assistantMessage.textContent = '```json\n' + data.raw + '\n```';
    } else {
      assistantMessage.textContent = `Не удалось сформировать валидный JSON.\n\nОшибка: ${data.error}\n\nПоследний ответ модели:\n${data.raw || ''}`;
    }
  } catch (error) {
    assistantMessage.textContent = `Ошибка генерации JSON: ${error.message}`;
  } finally {
    state.isStreaming = false;
    sendBtn.disabled = false;
    planBtn.disabled = false;
    jsonBtn.disabled = false;
    await loadConversations();
  }
}

async function readEventStream(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sepIndex;
    while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      const parsed = parseSseEvent(rawEvent);
      if (parsed) onEvent(parsed.event, parsed.data);
    }
  }
}

function parseSseEvent(raw) {
  const lines = raw.split('\n');
  let event = 'message';
  let data = '';
  for (const line of lines) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!data) return null;
  return { event, data: JSON.parse(data) };
}

function addMessage(role, content) {
  const el = document.createElement('div');
  el.className = `message ${role}`;
  el.textContent = content;
  messagesEl.appendChild(el);
  scrollMessages();
  return el;
}

function addStep(text) {
  const el = document.createElement('div');
  el.className = 'step';
  el.textContent = text;
  stepsBodyEl.appendChild(el);
  stepsEl.scrollTop = stepsEl.scrollHeight;
}

function scrollMessages() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
