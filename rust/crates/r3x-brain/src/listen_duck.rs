//! The music ducks while the guest talks. Replies already duck their own speech (`plan.rs`
//! `speak`), but pressing talk over a playing track left it at full volume: loud for the guest
//! and in the mic (2026-10-01: "can you stop playing the music now?" said over Utinni).
//!
//! A pure state machine over the turn's events (time passed in), driven by [`spawn`]:
//! - talk starts with music playing: duck;
//! - talk ends with nothing heard: unduck;
//! - talk ends with words: hold until that turn's reply starts speaking (its plan then owns the
//!   duck and unducks after it), or [`REPLY_WAIT_S`] without one (a silent command, a failed or
//!   superseded turn): unduck;
//! - anything else unducking meanwhile (a cut-off line's plan ending as talk interrupts it, DJ
//!   commentary finishing): duck again.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Act {
    Duck,
    Unduck,
}

/// How long a heard turn holds the duck waiting for its reply to start.
pub const REPLY_WAIT_S: f64 = 8.0;

#[derive(Debug, Default, PartialEq)]
enum Hold {
    #[default]
    Idle,
    /// Talk is held (`turn`).
    Listening { turn: String },
    /// Talk ended with words; waiting for `turn`'s reply to speak, until `until`.
    Awaiting { turn: String, until: f64 },
}

#[derive(Debug, Default)]
pub struct ListenDuck {
    hold: Hold,
    /// This layer ducked the music and has not restored it.
    ducked: bool,
}

impl ListenDuck {
    pub fn listening_started(&mut self, turn: &str, music_playing: bool) -> Option<Act> {
        self.hold = Hold::Listening { turn: turn.into() };
        if music_playing && !self.ducked {
            self.ducked = true;
            return Some(Act::Duck);
        }
        None
    }

    pub fn listening_stopped(&mut self, turn: &str, heard: bool, now: f64) -> Option<Act> {
        if !matches!(&self.hold, Hold::Listening { turn: t } if t == turn) {
            return None; // a stale stop
        }
        if heard {
            self.hold = Hold::Awaiting { turn: turn.into(), until: now + REPLY_WAIT_S };
            None
        } else {
            self.release()
        }
    }

    /// Speech began for `turn`: its reply, if this layer was waiting for it, now owns the duck.
    pub fn speech_started(&mut self, turn: &str) {
        if matches!(&self.hold, Hold::Awaiting { turn: t, .. } if t == turn) {
            self.hold = Hold::Idle;
            self.ducked = false;
        }
    }

    /// Something else restored the music while this layer holds it down: duck again.
    pub fn unducked_elsewhere(&mut self) -> Option<Act> {
        (self.ducked && self.hold != Hold::Idle).then_some(Act::Duck)
    }

    pub fn music_stopped(&mut self) {
        self.ducked = false;
    }

    pub fn tick(&mut self, now: f64) -> Option<Act> {
        match self.hold {
            Hold::Awaiting { until, .. } if now >= until => self.release(),
            _ => None,
        }
    }

    fn release(&mut self) -> Option<Act> {
        self.hold = Hold::Idle;
        std::mem::take(&mut self.ducked).then_some(Act::Unduck)
    }
}

/// Run the layer on the bus: conversation and music events in, `music.duck`/`unduck` out (at
/// the replies' level and fade), checked every 250 ms for an expired wait.
pub fn spawn(bus: &r3x_bus::Bus) -> tokio::task::JoinHandle<()> {
    use r3x_bus::Received;
    use r3x_contracts::{Body, ConversationEvent, Domain, Event, MusicEvent, MusicState, Source};
    let (mut conv, mut music) = (bus.subscribe(Domain::Conversation), bus.subscribe(Domain::Music));
    let bus = bus.clone();
    tokio::spawn(async move {
        let mut d = ListenDuck::default();
        let clock = bus.clock();
        let mut tick = tokio::time::interval(std::time::Duration::from_millis(250));
        let act = |a: Option<Act>| {
            let e = match a {
                Some(Act::Duck) => MusicEvent::Duck { level: crate::plan::DUCK_LEVEL, fade_ms: crate::plan::DUCK_FADE_MS },
                Some(Act::Unduck) => MusicEvent::Unduck { fade_ms: crate::plan::DUCK_FADE_MS },
                None => return,
            };
            bus.publish(Source::System, None, Event::Music(e));
        };
        loop {
            let m = tokio::select! {
                m = conv.recv() => m,
                m = music.recv() => m,
                _ = tick.tick() => {
                    act(d.tick(clock.t_mono()));
                    continue;
                }
            };
            let Some(Received::Message(env)) = m else {
                if m.is_none() {
                    return;
                }
                continue;
            };
            let turn = env.conversation_id.clone().unwrap_or_default();
            let Body::Event(e) = &env.body else { continue };
            match e {
                Event::Conversation(ConversationEvent::ListeningStarted) => act(d.listening_started(&turn, bus.get::<MusicState>().playing)),
                Event::Conversation(ConversationEvent::ListeningStopped { transcript }) => {
                    act(d.listening_stopped(&turn, !transcript.trim().is_empty(), clock.t_mono()))
                }
                Event::Conversation(ConversationEvent::SpeechStarted) => d.speech_started(&turn),
                Event::Music(MusicEvent::Unducked) => act(d.unducked_elsewhere()),
                Event::Music(MusicEvent::TrackStopped) => d.music_stopped(),
                _ => {}
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn talking_over_music_ducks_and_an_empty_press_restores_it() {
        let mut d = ListenDuck::default();
        assert_eq!(d.listening_started("t1", true), Some(Act::Duck));
        assert_eq!(d.listening_stopped("t1", false, 1.0), Some(Act::Unduck), "nothing heard: music back");
        assert_eq!(d.tick(20.0), None, "and nothing more");
    }

    #[test]
    fn no_music_no_duck() {
        let mut d = ListenDuck::default();
        assert_eq!(d.listening_started("t1", false), None);
        assert_eq!(d.listening_stopped("t1", false, 1.0), None, "never ducked: nothing to restore");
    }

    #[test]
    fn a_heard_turn_hands_the_duck_to_its_reply() {
        let mut d = ListenDuck::default();
        assert_eq!(d.listening_started("t1", true), Some(Act::Duck));
        assert_eq!(d.listening_stopped("t1", true, 10.0), None, "words: hold for the reply");
        assert_eq!(d.tick(12.0), None);
        d.speech_started("t1");
        assert_eq!(d.tick(30.0), None, "the reply's plan owns it now (it unducks after speaking)");
        assert_eq!(d.unducked_elsewhere(), None, "and its unduck is not fought");
    }

    #[test]
    fn a_heard_turn_with_no_reply_restores_the_music_after_the_wait() {
        let mut d = ListenDuck::default();
        d.listening_started("t1", true);
        d.listening_stopped("t1", true, 10.0);
        assert_eq!(d.tick(10.0 + REPLY_WAIT_S - 0.1), None);
        assert_eq!(d.tick(10.0 + REPLY_WAIT_S + 0.1), Some(Act::Unduck), "a silent command or a dropped turn");
        assert_eq!(d.tick(40.0), None, "once");
    }

    #[test]
    fn an_unduck_from_elsewhere_while_talking_is_undone() {
        let mut d = ListenDuck::default();
        d.listening_started("t1", true);
        assert_eq!(d.unducked_elsewhere(), Some(Act::Duck), "the interrupted line's plan unducked: duck again");
        d.listening_stopped("t1", true, 5.0);
        assert_eq!(d.unducked_elsewhere(), Some(Act::Duck), "still waiting for the reply");
    }

    #[test]
    fn a_reply_for_another_turn_does_not_release_the_hold() {
        let mut d = ListenDuck::default();
        d.listening_started("t2", true);
        d.listening_stopped("t2", true, 5.0);
        d.speech_started("t1");
        assert_eq!(d.tick(5.0 + REPLY_WAIT_S + 0.1), Some(Act::Unduck), "t1's speech is not t2's reply");
    }

    #[test]
    fn music_stopping_meanwhile_leaves_nothing_to_restore() {
        let mut d = ListenDuck::default();
        d.listening_started("t1", true);
        d.music_stopped();
        assert_eq!(d.listening_stopped("t1", false, 1.0), None);
        assert_eq!(d.unducked_elsewhere(), None);
    }

    #[test]
    fn talking_again_while_waiting_keeps_one_duck() {
        let mut d = ListenDuck::default();
        d.listening_started("t1", true);
        d.listening_stopped("t1", true, 1.0);
        assert_eq!(d.listening_started("t2", true), None, "already ducked: no second duck");
        assert_eq!(d.listening_stopped("t2", false, 2.0), Some(Act::Unduck), "the newest press decides");
    }
}
