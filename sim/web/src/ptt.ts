/**
 * Push-to-talk on the panel, as a pure state machine (tested in test/ptt.test.ts).
 *
 * - The talk button toggles: click to start, click again to stop and send.
 * - Space is hold-to-talk: keydown starts, keyup stops. Auto-repeat and keys typed into a
 *   field are ignored.
 * - One owner (`state.conversation.ptt_owner`): the runtime owns the rule, the panel only
 *   mirrors it. A turn someone else holds (the "click anywhere" mouse, the CLI) is shown, and
 *   the button does not try to stop it.
 */

import type { ConversationPhase } from './generated/ConversationPhase';

/** Our owner name in `ptt_owner` (the runtime's name for the `ui` source). */
export const OWNER = 'ui';

export type PttSend = 'ptt_start' | 'ptt_stop';
export type PttMode = 'click' | 'hold';

export interface PttState {
  connected: boolean;
  /** Stage lets turns in (the brain is on). */
  enabled: boolean;
  phase: ConversationPhase;
  owner: string | null;
  /** A start/stop sent and not yet acked. */
  pending: 'start' | 'stop' | null;
  /** How our current (or starting) turn was started. */
  mode: PttMode | null;
  /** Our start was acked and the turn has not ended (the ack can beat the state update). */
  live: boolean;
  /** Space went up while the start was still pending: stop as soon as it is acked. */
  stopWhenStarted: boolean;
  spaceHeld: boolean;
}

export type PttInput =
  | { kind: 'click' }
  | { kind: 'space_down'; repeat: boolean; typing: boolean }
  | { kind: 'space_up' }
  | { kind: 'server'; phase: ConversationPhase; owner: string | null }
  | { kind: 'ack'; of: 'start' | 'stop'; ok: boolean }
  | { kind: 'link'; connected: boolean }
  | { kind: 'enabled'; enabled: boolean };

export interface PttStep {
  state: PttState;
  send?: PttSend;
  /** The key event was ours (preventDefault). */
  handled?: boolean;
}

export const initial = (): PttState => ({
  connected: false, enabled: true, phase: 'idle', owner: null, pending: null, mode: null, live: false, stopWhenStarted: false,
  spaceHeld: false,
});

const listeningForUs = (s: PttState) => s.phase === 'listening' && s.owner === OWNER;
const ours = (s: PttState) => s.live || listeningForUs(s);
/** A new turn may start: idle, thinking (a follow-up while R3X is still composing), or
 * speaking - talking over R3X interrupts him (the runtime stops his speech before the mic opens). */
const canStart = (s: PttState) =>
  s.connected && s.enabled && !s.pending && (s.phase === 'idle' || s.phase === 'thinking' || s.phase === 'speaking');

export function step(s: PttState, i: PttInput): PttStep {
  switch (i.kind) {
    case 'link':
      return { state: i.connected ? { ...s, connected: true } : { ...initial(), enabled: s.enabled } };
    case 'enabled':
      return { state: { ...s, enabled: i.enabled } };
    case 'server': {
      const next = { ...s, phase: i.phase, owner: i.owner };
      // Our turn ended (sent, or stopped elsewhere).
      if (listeningForUs(s) && !listeningForUs(next)) Object.assign(next, { live: false, mode: next.pending === 'start' ? next.mode : null });
      return { state: next };
    }
    case 'ack': {
      const next = { ...s, pending: null };
      if (i.of === 'start' && !i.ok) return { state: { ...next, mode: null, live: false, stopWhenStarted: false } };
      if (i.of === 'start' && s.stopWhenStarted) {
        return { state: { ...next, live: true, pending: 'stop', stopWhenStarted: false }, send: 'ptt_stop' };
      }
      if (i.of === 'start') next.live = true;
      if (i.of === 'stop') Object.assign(next, { live: false, mode: null });
      return { state: next };
    }
    case 'click':
      if (s.pending) return { state: s };
      if (ours(s)) return { state: { ...s, pending: 'stop' }, send: 'ptt_stop' };
      if (canStart(s)) return { state: { ...s, pending: 'start', mode: 'click' }, send: 'ptt_start' };
      return { state: s };
    case 'space_down':
      if (i.typing) return { state: s };
      if (i.repeat || s.spaceHeld) return { state: s, handled: true };
      if (ours(s) && !s.pending) {
        // Space during a clicked turn sends it (keyup then does nothing).
        return { state: { ...s, pending: 'stop' }, send: 'ptt_stop', handled: true };
      }
      if (canStart(s)) return { state: { ...s, pending: 'start', mode: 'hold', spaceHeld: true }, send: 'ptt_start', handled: true };
      return { state: s, handled: true };
    case 'space_up': {
      if (!s.spaceHeld) return { state: s };
      const next = { ...s, spaceHeld: false };
      if (s.pending === 'start' && s.mode === 'hold') return { state: { ...next, stopWhenStarted: true }, handled: true };
      if (ours(s) && s.mode === 'hold' && !s.pending) return { state: { ...next, pending: 'stop' }, send: 'ptt_stop', handled: true };
      return { state: next, handled: true };
    }
  }
}

export type PttLook = 'offline' | 'idle' | 'starting' | 'listening' | 'sending' | 'thinking' | 'speaking' | 'busy';

/** The button: a `data-state` for CSS, the label, and the hint under it. */
export function view(s: PttState): { look: PttLook; label: string; hint: string } {
  if (!s.connected) return { look: 'offline', label: 'Offline', hint: './r3x' };
  if (!s.enabled) return { look: 'offline', label: 'Talk is off in this mode', hint: '' };
  if (s.pending === 'start') return { look: 'starting', label: 'Starting mic…', hint: '' };
  if (s.pending === 'stop') return { look: 'sending', label: 'Sending…', hint: '' };
  if (ours(s) || s.phase === 'listening') {
    if (ours(s)) {
      return s.mode === 'hold'
        ? { look: 'listening', label: 'Listening… release to send', hint: '' }
        : { look: 'listening', label: 'Listening… click to send', hint: '' };
    }
    const who = s.owner === 'mouse' ? 'click-anywhere' : s.owner ?? 'another client';
    return { look: 'busy', label: `Listening (${who})`, hint: '' };
  }
  if (s.phase === 'thinking') return { look: 'thinking', label: 'Thinking…', hint: '' };
  if (s.phase === 'speaking') return { look: 'speaking', label: 'R3X is talking', hint: 'click to interrupt' };
  return { look: 'idle', label: 'Click to talk', hint: 'or hold Space' };
}
