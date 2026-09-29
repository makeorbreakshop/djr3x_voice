/**
 * `voice.html`: a standalone talk-to-R3X page (hold-to-talk + playback) that a dashboard such
 * as Lobot can iframe. Token in the URL fragment (`#token=...`); `?gw=host:port` picks the
 * gateway (default: this host, port 8780).
 */

import { GatewayClient, gatewayUrl } from '../gateway';
import { RemoteVoice } from './remote';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const token = new URLSearchParams(location.hash.slice(1)).get('token') ?? '';
const gw = new GatewayClient(gatewayUrl(token));
const btn = $<HTMLButtonElement>('talk');
const status = $('status');
const heard = $('heard');
const said = $('said');
const mouth = $('mouth');

const voice = new RemoteVoice(gw, {
  onState(s, detail) {
    btn.dataset.state = s;
    btn.textContent = s === 'listening' ? 'Listening… release to send' : s === 'starting' ? 'Starting…' : 'Hold to talk';
    if (s === 'refused') status.textContent = `Not listening: ${detail ?? ''}`;
  },
});

gw.subscribe({
  onStatus(on) {
    btn.disabled = !on;
    status.textContent = on ? 'Connected to R3X' : 'Connecting to the r3x gateway…';
  },
  onEvent(e) {
    if (e.domain !== 'conversation') return;
    switch (e.type) {
      case 'transcript':
        heard.textContent = e.text;
        break;
      case 'listening_stopped':
        heard.textContent = e.transcript || '(nothing heard)';
        said.textContent = '…';
        break;
      case 'reply':
        said.textContent = e.text;
        break;
      case 'mouth':
        mouth.style.transform = `scaleY(${0.1 + 0.9 * e.level})`;
        break;
      case 'speech_ended':
        mouth.style.transform = 'scaleY(0.1)';
        break;
    }
  },
});

const press = (e: Event) => {
  e.preventDefault();
  void voice.press();
};
const release = () => void voice.release();
btn.addEventListener('pointerdown', (e) => {
  btn.setPointerCapture(e.pointerId);
  press(e);
});
btn.addEventListener('pointerup', release);
btn.addEventListener('pointercancel', release);
btn.addEventListener('contextmenu', (e) => e.preventDefault());
window.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !e.repeat) press(e);
});
window.addEventListener('keyup', (e) => {
  if (e.code === 'Space') release();
});

if (!token) status.textContent = 'Missing #token= in the URL';
gw.start();
