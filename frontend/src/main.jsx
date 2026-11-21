import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowRight, ArrowUp, BookOpen, Check, CircleAlert, Code2, Copy,
  Database, Feather, Layers2, MessageSquare, Plus, Settings2, Sparkles, X, Zap,
} from 'lucide-react';

import './styles.css';

const API = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const MAX_MESSAGE_LENGTH = 4000;
const SUGGESTIONS = [
  { icon: BookOpen, label: 'Learn a concept', prompt: 'Explain how an LLM response cache works in simple terms.' },
  { icon: Feather, label: 'Draft an email', prompt: 'Write a short, professional email asking to reschedule a meeting.' },
  { icon: Code2, label: 'Review some code', prompt: 'Show me a small Python function for checking palindromes, then explain its edge cases.' },
];

function createSession() {
  return crypto.randomUUID();
}

async function readReply(response) {
  const data = await response.json().catch(() => ({}));
  if (response.ok && typeof data.reply === 'string' && data.metadata) return data;
  if (response.status === 503) {
    throw new Error('The AI providers are temporarily unavailable. Try sending your message again in a moment.');
  }
  if (response.status === 422) {
    const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
    throw new Error(detail || 'Check your message and try again.');
  }
  throw new Error('The chat service could not complete this request. Try again in a moment.');
}

function Metadata({ metadata }) {
  const cached = metadata.resolution_source === 'LLM_CACHE';
  const SourceIcon = cached ? Database : Zap;

  return (
    <dl className="metadata">
      <div className={cached ? 'source cached' : 'source'}>
        <dt className="sr-only">Source</dt>
        <dd><SourceIcon size={13} aria-hidden="true" /> <span data-testid="meta-source">{metadata.resolution_source}</span></dd>
      </div>
      <div><dt>Model</dt><dd data-testid="meta-model">{metadata.model}</dd></div>
      <div><dt>Latency</dt><dd data-testid="meta-latency">{metadata.latency_ms} ms</dd></div>
      <div><dt>Input</dt><dd data-testid="meta-input-tokens">{metadata.input_tokens}</dd></div>
      <div><dt>Output</dt><dd data-testid="meta-output-tokens">{metadata.output_tokens}</dd></div>
    </dl>
  );
}

function AssistantMessage({ reply, metadata }) {
  const [copyStatus, setCopyStatus] = useState('Copy reply');
  const body = useRef(null);

  async function copyReply() {
    try {
      await navigator.clipboard.writeText(body.current.innerText);
      setCopyStatus('Copied');
    } catch {
      setCopyStatus('Copy unavailable');
    }
  }

  return (
    <article className="message assistant" data-testid="assistant-message">
      <div className="message-heading">
        <span><Sparkles size={16} /> Lyftr</span>
        <button className="icon-button" onClick={copyReply} aria-label={copyStatus} title={copyStatus}>
          {copyStatus === 'Copied' ? <Check size={16} /> : <Copy size={16} />}
        </button>
      </div>
      <div className="assistant-body" ref={body}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img']}>
          {reply}
        </ReactMarkdown>
      </div>
      <Metadata metadata={metadata} />
    </article>
  );
}

function App() {
  const [sessionId, setSessionId] = useState(createSession);
  const [draft, setDraft] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [promptOpen, setPromptOpen] = useState(false);
  const [turns, setTurns] = useState([]);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const [connection, setConnection] = useState('checking');
  const request = useRef(null);
  const input = useRef(null);
  const scrollPane = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API}/health`, { signal: controller.signal })
      .then((res) => setConnection(res.ok ? 'online' : 'offline'))
      .catch(() => {
        if (!controller.signal.aborted) setConnection('offline');
      });
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, []);

  useEffect(() => {
    const pane = scrollPane.current;
    pane.scrollTo({ top: pane.scrollHeight, behavior: 'instant' });
  }, [turns, pending, error]);

  function newChat() {
    request.current?.abort();
    request.current = null;
    setSessionId(createSession());
    setDraft('');
    setSystemPrompt('');
    setPromptOpen(false);
    setTurns([]);
    setPending(null);
    setError(null);
    input.current?.focus();
  }

  const draftLength = Array.from(draft).length;
  const message = draft.trim();
  const canSend = Boolean(message) && Array.from(message).length <= MAX_MESSAGE_LENGTH;

  async function send(event) {
    event.preventDefault();
    if (request.current || !canSend) return;

    const controller = new AbortController();
    request.current = controller;
    setDraft('');
    setError(null);
    setPending(message);
    const payload = { session_id: sessionId, message };
    if (systemPrompt.trim()) payload.system_prompt = systemPrompt.trim();
    else if (turns.at(-1)?.systemPrompt) payload.system_prompt = '';

    try {
      const response = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      const data = await readReply(response);
      if (request.current !== controller) return;
      setConnection('online');
      setTurns((current) => [...current, {
        message, reply: data.reply, metadata: data.metadata, systemPrompt: payload.system_prompt,
      }]);
    } catch (failure) {
      if (request.current !== controller) return;
      if (failure instanceof TypeError) setConnection('offline');
      setError({
        message,
        detail: failure instanceof TypeError
          ? 'Could not reach the chat service. Check your connection and try again.'
          : failure.message,
      });
    } finally {
      if (request.current === controller) {
        request.current = null;
        setPending(null);
        input.current?.focus();
      }
    }
  }

  const welcome = !turns.length && !pending && !error;
  const statusText = { online: 'API online', offline: 'API unavailable', checking: 'Checking API' };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-icon"><Layers2 size={23} strokeWidth={2.1} /></span>
          <span>Lyftr Chat</span>
        </div>
        <button className="new-chat" onClick={newChat}><Plus size={18} /> New chat</button>
        <div className="current-chat"><MessageSquare size={17} /> Current session</div>
        <div className="sidebar-bottom">
          <span className={`connection ${connection}`} role="status">
            <span className="connection-dot" />{statusText[connection]}
          </span>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="header-title"><MessageSquare size={17} /> Conversation</div>
          <div className="header-actions">
            <button className="mobile-new icon-button" onClick={newChat} aria-label="New chat" title="New chat"><Plus size={20} /></button>
            <button className="settings-button" onClick={() => setPromptOpen(!promptOpen)}
              aria-expanded={promptOpen} aria-controls="session-settings">
              <Settings2 size={16} /> <span>Session settings</span>
            </button>
          </div>
        </header>

        {promptOpen && (
          <section className="settings-panel" id="session-settings">
            <div className="settings-heading">
              <label htmlFor="system-prompt">System prompt</label>
              <button className="icon-button" onClick={() => setPromptOpen(false)} aria-label="Close session settings"><X size={17} /></button>
            </div>
            <textarea id="system-prompt" data-testid="system-prompt-input" rows="3" maxLength="4000"
              value={systemPrompt} placeholder="Optional instructions for the assistant"
              onChange={(event) => setSystemPrompt(event.target.value)} />
            <small>Applies to your next message in this session.</small>
          </section>
        )}

        <div className={`scroll-pane ${welcome ? 'welcome-pane' : ''}`} ref={scrollPane}>
          {welcome ? (
            <section className="welcome content-width">
              <div className="welcome-icon"><Sparkles size={27} strokeWidth={1.8} /></div>
              <h1>How can I help?</h1>
              <p>Ask a question or start with one of these prompts.</p>
              <div className="suggestions">
                {SUGGESTIONS.map(({ icon: Icon, label, prompt }) => (
                  <button className="suggestion" key={label} onClick={() => { setDraft(prompt); input.current?.focus(); }}>
                    <Icon size={17} /><span>{label}</span><ArrowRight size={15} className="suggestion-arrow" />
                  </button>
                ))}
              </div>
            </section>
          ) : (
            <section className="transcript content-width" aria-label="Messages" aria-live="polite">
              {turns.map((turn, index) => (
                <React.Fragment key={index}>
                  <article className="message user"><div className="message-body">{turn.message}</div></article>
                  <AssistantMessage reply={turn.reply} metadata={turn.metadata} />
                </React.Fragment>
              ))}
              {pending && <>
                <article className="message user"><div className="message-body">{pending}</div></article>
                <div className="thinking" role="status"><span /> Generating a reply…</div>
              </>}
              {error && <>
                <article className="message user"><div className="message-body">{error.message}</div></article>
                <div className="error" role="alert">
                  <CircleAlert size={18} aria-hidden="true" />
                  <div>{error.detail} <button onClick={() => { setDraft(error.message); setError(null); input.current?.focus(); }}>Restore message</button></div>
                </div>
              </>}
            </section>
          )}
        </div>

        <div className="composer-area content-width">
          <form className="composer" onSubmit={send}>
            <textarea
              ref={input} aria-label="Message" data-testid="chat-input" placeholder="Message Lyftr…"
              rows="2" value={draft} aria-invalid={message.length > 0 && !canSend}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) send(event);
              }}
            />
            <div className="composer-actions">
              <span className={message && !canSend ? 'limit-error' : undefined}>{draftLength.toLocaleString('en-US')} / 4,000</span>
              <button className="send" type="submit" data-testid="send-button" aria-label="Send message"
                disabled={!canSend || pending !== null}><ArrowUp size={20} strokeWidth={2.4} /></button>
            </div>
          </form>
          <p className="composer-hint">Enter to send <span>·</span> Shift + Enter for a new line</p>
        </div>
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
