import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bot,
  Copy,
  KeyRound,
  MessageCirclePlus,
  MoreHorizontal,
  RotateCcw,
  Save,
  SendHorizontal,
  Server,
  Settings,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  UserRound,
  Wrench,
  X,
} from "lucide-react";
import { deleteSettings, getSettings, saveSettings, sendChatMessage } from "./api";
import "./styles.css";

const SESSIONS_KEY = "generic-mcp-chat-sessions";
const ACTIVE_SESSION_KEY = "generic-mcp-active-session";

const welcomeMessage = {
  role: "assistant",
  content: "Hi, I can help with questions and use your MCP tools when live server data is needed.",
};

function createSession() {
  const now = new Date().toISOString();
  return {
    id: crypto.randomUUID(),
    title: "New chat",
    createdAt: now,
    updatedAt: now,
    messages: [welcomeMessage],
  };
}

function loadSessions() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SESSIONS_KEY) || "[]");
    if (Array.isArray(parsed) && parsed.length > 0) {
      return parsed;
    }
  } catch {
    localStorage.removeItem(SESSIONS_KEY);
  }
  return [createSession()];
}

function sessionTitle(messages) {
  const firstUserMessage = messages.find((message) => message.role === "user");
  if (!firstUserMessage) return "New chat";
  return firstUserMessage.content.slice(0, 42);
}

function isWelcomeMessage(message) {
  return message.role === welcomeMessage.role && message.content === welcomeMessage.content;
}

function App() {
  const [sessions, setSessions] = useState(loadSessions);
  const [activeSessionId, setActiveSessionId] = useState(
    () => localStorage.getItem(ACTIVE_SESSION_KEY) || sessions[0].id,
  );
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const scrollRef = useRef(null);

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) || sessions[0],
    [activeSessionId, sessions],
  );
  const messages = activeSession?.messages || [welcomeMessage];
  const isWelcomeOnly = messages.length === 1 && isWelcomeMessage(messages[0]);

  useEffect(() => {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
  }, [sessions]);

  useEffect(() => {
    localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
  }, [activeSessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, isLoading]);

  function updateActiveSession(nextMessages) {
    setSessions((current) =>
      current.map((session) =>
        session.id === activeSession.id
          ? {
              ...session,
              title: sessionTitle(nextMessages),
              updatedAt: new Date().toISOString(),
              messages: nextMessages,
            }
          : session,
      ),
    );
  }

  function startNewChat() {
    const session = createSession();
    setSessions((current) => [session, ...current]);
    setActiveSessionId(session.id);
    setInput("");
    setError("");
  }

  function deleteSession(sessionId) {
    setSessions((current) => {
      if (current.length === 1) {
        const replacement = createSession();
        setActiveSessionId(replacement.id);
        return [replacement];
      }
      const remaining = current.filter((session) => session.id !== sessionId);
      if (sessionId === activeSessionId) {
        setActiveSessionId(remaining[0].id);
      }
      return remaining;
    });
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;

    const userMessage = { role: "user", content: trimmed };
    const userMessages = [...messages, userMessage];
    const history = messages.filter((message) => !isWelcomeMessage(message));

    updateActiveSession(userMessages);
    setInput("");
    setError("");
    setIsLoading(true);

    try {
      const result = await sendChatMessage(trimmed, history);
      updateActiveSession([
        ...userMessages,
        responseToMessage(result, trimmed, history),
      ]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Something went wrong.";
      setError(message);
      updateActiveSession([
        ...userMessages,
        {
          role: "assistant",
          content: "I could not complete that request. Check the settings window and backend logs.",
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleToolApproval(message, approved) {
    if (!message.approvalId || isLoading) return;

    setError("");
    setIsLoading(true);
    const updatedMessages = messages.map((item) =>
      item === message ? { ...item, approvalResolved: true } : item,
    );
    updateActiveSession(updatedMessages);

    try {
      const result = await sendChatMessage(message.originalMessage || "", message.originalHistory || [], {
        approval_id: message.approvalId,
        approve_tool_calls: approved,
      });
      updateActiveSession([...updatedMessages, responseToMessage(result, message.originalMessage || "", message.originalHistory || [])]);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Something went wrong.";
      setError(errorMessage);
      updateActiveSession([
        ...updatedMessages,
        {
          role: "assistant",
          content: "I could not continue that MCP action. Check the backend logs.",
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="Previous chat sessions">
        <div className="sidebar-header">
          <div className="brand-mark" aria-hidden="true">
            <Sparkles size={22} />
          </div>
          <div>
            <h1>Generic MCP Host</h1>
            <p>MCP Host Chatbot</p>
          </div>
        </div>

        <button className="new-chat-button" type="button" onClick={startNewChat}>
          <MessageCirclePlus size={18} />
          New chat
        </button>

        <div className="session-list" aria-label="Chat history">
          {sessions.map((session) => (
            <button
              className={`session-item ${session.id === activeSession.id ? "active" : ""}`}
              type="button"
              key={session.id}
              onClick={() => setActiveSessionId(session.id)}
            >
              <span>{session.title}</span>
              <small>{new Date(session.updatedAt).toLocaleDateString()}</small>
              <Trash2
                size={15}
                className="session-delete"
                onClick={(event) => {
                  event.stopPropagation();
                  deleteSession(session.id);
                }}
              />
            </button>
          ))}
        </div>

        <button className="settings-button" type="button" onClick={() => setIsSettingsOpen(true)}>
          <Settings size={18} />
          Settings
        </button>
      </aside>

      <section className={`chat-panel ${isWelcomeOnly ? "empty-chat" : ""}`} aria-label="MCP host chatbot">
        <header className="topbar">
          <div>
            <h2>MCP Host Chatbot</h2>
            <p>Chat with dynamic MCP tools.</p>
          </div>
          <button type="button" onClick={() => setIsSettingsOpen(true)} aria-label="Open settings">
            <Settings size={19} />
          </button>
        </header>

        <div className="conversation" ref={scrollRef}>
          <div className={`message-list ${isWelcomeOnly ? "welcome-list" : ""}`}>
            {isWelcomeOnly ? (
              <div className="welcome-hero">
                <div className="welcome-star" aria-hidden="true">
                  <Sparkles size={24} />
                </div>
                <h2>What should we focus on?</h2>
              </div>
            ) : (
              messages.map((message, index) => (
                <ChatBubble
                  key={`${message.role}-${index}`}
                  message={message}
                  onToolApproval={handleToolApproval}
                  isLoading={isLoading}
                />
              ))
            )}

            {isLoading && (
              <div className="message-row assistant">
                <div className="avatar assistant-avatar">
                  <Sparkles size={18} />
                </div>
                <div className="bubble assistant-bubble loading-bubble">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}
          </div>
        </div>

        {error && <div className="error-strip">{error}</div>}

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                handleSubmit(event);
              }
            }}
            placeholder="Ask a question or request an action from your MCP tools..."
            rows={1}
            aria-label="Chat message"
          />
          <button type="submit" disabled={!input.trim() || isLoading} aria-label="Send message">
            <SendHorizontal size={20} />
          </button>
        </form>
      </section>

      {isSettingsOpen && <SettingsWindow onClose={() => setIsSettingsOpen(false)} />}
    </main>
  );
}

function responseToMessage(result, originalMessage, originalHistory) {
  return {
    role: "assistant",
    content: result.reply,
    usedTools: result.used_tools || [],
    toolDiscoveryError: result.tool_discovery_error,
    awaitingToolApproval: Boolean(result.awaiting_tool_approval),
    approvalId: result.approval_id,
    pendingToolCalls: result.pending_tool_calls || [],
    originalMessage,
    originalHistory,
  };
}

function ChatBubble({ message, onToolApproval, isLoading }) {
  const isUser = message.role === "user";
  return (
    <div className={`message-row ${isUser ? "user" : "assistant"}`}>
      {!isUser && (
        <div className="avatar assistant-avatar">
          <Bot size={18} />
        </div>
      )}
      <div className={`bubble ${isUser ? "user-bubble" : "assistant-bubble"}`}>
        <p>{message.content}</p>
        {message.usedTools?.length > 0 && (
          <div className="tool-chip-list">
            {message.usedTools.map((tool, index) => (
              <span className="tool-chip" key={`${tool.name}-${index}`}>
                <Wrench size={13} />
                {tool.name}
              </span>
            ))}
          </div>
        )}
        {message.awaitingToolApproval && (
          <div className="approval-card">
            <h3>Approve MCP call?</h3>
            <div className="approval-tool-list">
              {message.pendingToolCalls.map((tool) => (
                <div className="approval-tool" key={tool.id}>
                  <strong>{tool.name}</strong>
                  <span>{tool.kind}</span>
                  <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
                </div>
              ))}
            </div>
            <div className="approval-actions">
              <button
                type="button"
                disabled={message.approvalResolved || isLoading}
                onClick={() => onToolApproval(message, false)}
              >
                Deny
              </button>
              <button
                type="button"
                disabled={message.approvalResolved || isLoading}
                onClick={() => onToolApproval(message, true)}
              >
                Approve
              </button>
            </div>
          </div>
        )}
        {!isUser && (
          <div className="message-actions" aria-label="Message actions">
            <button type="button" aria-label="Like response">
              <ThumbsUp size={16} />
            </button>
            <button type="button" aria-label="Dislike response">
              <ThumbsDown size={16} />
            </button>
            <button type="button" aria-label="Regenerate response">
              <RotateCcw size={16} />
            </button>
            <button type="button" aria-label="Copy response">
              <Copy size={16} />
            </button>
            <button type="button" aria-label="More actions">
              <MoreHorizontal size={16} />
            </button>
          </div>
        )}
      </div>
      {isUser && (
        <div className="avatar user-avatar">
          <UserRound size={18} />
        </div>
      )}
    </div>
  );
}

function SettingsWindow({ onClose }) {
  const [form, setForm] = useState({
    groqApiKey: "",
    groqModel: "",
    mcpServerUrl: "",
    mcpServerCommand: "",
    mcpServerEnvJson: "",
    mcpTransport: "auto",
    mcpServersJson: "",
  });
  const [keyPreview, setKeyPreview] = useState("");
  const [hasSavedOverrides, setHasSavedOverrides] = useState(false);
  const [status, setStatus] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let mounted = true;
    getSettings()
      .then((settings) => {
        if (!mounted) return;
        setForm({
          groqApiKey: "",
          groqModel: settings.groq_model || "",
          mcpServerUrl: settings.mcp_server_url || "",
          mcpServerCommand: settings.mcp_server_command || "",
          mcpServerEnvJson: settings.mcp_server_env_json || "",
          mcpTransport: settings.mcp_transport || "auto",
          mcpServersJson: settings.mcp_servers_json || "",
        });
        setKeyPreview(settings.groq_api_key_preview || "");
        setHasSavedOverrides(Boolean(settings.has_saved_overrides));
      })
      .catch((err) => setStatus(err instanceof Error ? err.message : "Could not load settings."));
    return () => {
      mounted = false;
    };
  }, []);

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function handleSave(event) {
    event.preventDefault();
    setStatus("");
    setIsSaving(true);

    const payload = {
      groq_model: form.groqModel,
      mcp_server_url: form.mcpServerUrl,
      mcp_server_command: form.mcpServerCommand,
      mcp_server_env_json: form.mcpServerEnvJson,
      mcp_transport: form.mcpTransport,
      mcp_servers_json: form.mcpServersJson,
    };
    if (form.groqApiKey.trim()) {
      payload.groq_api_key = form.groqApiKey;
    }

    try {
      const settings = await saveSettings(payload);
      setKeyPreview(settings.groq_api_key_preview || "");
      setHasSavedOverrides(Boolean(settings.has_saved_overrides));
      setForm((current) => ({ ...current, groqApiKey: "" }));
      setStatus("Settings saved. New chats will use these values immediately.");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Could not save settings.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleDelete() {
    setStatus("");
    setIsSaving(true);
    try {
      const settings = await deleteSettings();
      setForm({
        groqApiKey: "",
        groqModel: settings.groq_model || "",
        mcpServerUrl: settings.mcp_server_url || "",
        mcpServerCommand: settings.mcp_server_command || "",
        mcpServerEnvJson: settings.mcp_server_env_json || "",
        mcpTransport: settings.mcp_transport || "auto",
        mcpServersJson: settings.mcp_servers_json || "",
      });
      setKeyPreview(settings.groq_api_key_preview || "");
      setHasSavedOverrides(Boolean(settings.has_saved_overrides));
      setStatus("Saved UI settings deleted. The app is using .env defaults again.");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Could not delete settings.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="settings-overlay" role="presentation">
      <section className="settings-window" role="dialog" aria-modal="true" aria-label="Connection settings">
        <header className="settings-header">
          <div>
            <span className="settings-kicker">Runtime settings</span>
            <h2>Connections</h2>
            <p>Configure Groq and your MCP servers without editing files.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close settings">
            <X size={20} />
          </button>
        </header>

        <form className="settings-content" onSubmit={handleSave}>
          <div className="settings-group">
            <div className="settings-group-title">
              <KeyRound size={18} />
              <h3>Groq</h3>
            </div>
            <label>
              API key
              <input
                type="password"
                value={form.groqApiKey}
                onChange={(event) => updateField("groqApiKey", event.target.value)}
                placeholder={keyPreview ? `Current: ${keyPreview}` : "Enter Groq API key"}
                autoComplete="off"
              />
            </label>
            <label>
              Model
              <input
                value={form.groqModel}
                onChange={(event) => updateField("groqModel", event.target.value)}
                placeholder="llama-3.3-70b-versatile"
              />
            </label>
          </div>

          <div className="settings-group">
            <div className="settings-group-title">
              <Server size={18} />
              <h3>MCP server</h3>
            </div>
            <label>
              MCP servers JSON
              <textarea
                value={form.mcpServersJson}
                onChange={(event) => updateField("mcpServersJson", event.target.value)}
                placeholder='[{"name":"crm","url":"http://localhost:8001/mcp","transport":"streamable_http"},{"name":"docs","command":"python /absolute/path/server.py","env":{"TOKEN":"value"}}]'
                rows={6}
              />
            </label>
            <label>
              Server URL
              <input
                value={form.mcpServerUrl}
                onChange={(event) => updateField("mcpServerUrl", event.target.value)}
                placeholder="http://localhost:8001/mcp"
              />
            </label>
            <label>
              Server command
              <textarea
                value={form.mcpServerCommand}
                onChange={(event) => updateField("mcpServerCommand", event.target.value)}
                placeholder="/Users/lalith/.local/bin/uv --directory /path/to/server run server.py"
                rows={3}
              />
            </label>
            <label>
              Server env JSON
              <textarea
                value={form.mcpServerEnvJson}
                onChange={(event) => updateField("mcpServerEnvJson", event.target.value)}
                placeholder='{"UV_CACHE_DIR":"/path/to/cache"}'
                rows={4}
              />
            </label>
            <label>
              Transport
              <select
                value={form.mcpTransport}
                onChange={(event) => updateField("mcpTransport", event.target.value)}
              >
                <option value="auto">auto</option>
                <option value="streamable_http">streamable_http</option>
                <option value="sse">sse</option>
              </select>
            </label>
          </div>

          <div className="settings-status-row">
            <span>{hasSavedOverrides ? "Using saved UI overrides" : "Using .env defaults"}</span>
            {status && <p>{status}</p>}
          </div>

          <footer className="settings-actions">
            <button className="danger-button" type="button" onClick={handleDelete} disabled={isSaving}>
              <Trash2 size={17} />
              Delete settings
            </button>
            <button className="save-button" type="submit" disabled={isSaving}>
              <Save size={17} />
              Save settings
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
