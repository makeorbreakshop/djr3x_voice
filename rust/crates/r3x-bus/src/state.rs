use r3x_contracts::{
    ConversationState, DjState, EngagementState, LightsState, MusicState, PerfState,
    RetainedState, ServicesState, StageState, StateUpdate,
};
use tokio::sync::watch;

/// A retained-state domain with its own `watch` cell on the bus.
pub trait StateDomain: Clone + PartialEq + Send + Sync + 'static {
    /// Used in the session-log topic `state.<NAME>`.
    const NAME: &'static str;
    #[doc(hidden)]
    fn cell(states: &States) -> &watch::Sender<Self>;
    fn into_update(self) -> StateUpdate;
}

macro_rules! domains {
    ($($field:ident: $ty:ident => $variant:ident),* $(,)?) => {
        #[doc(hidden)]
        pub struct States { $($field: watch::Sender<$ty>,)* }

        impl Default for States {
            fn default() -> Self {
                Self { $($field: watch::Sender::new($ty::default()),)* }
            }
        }

        impl States {
            pub(crate) fn snapshot(&self) -> RetainedState {
                RetainedState { $($field: self.$field.borrow().clone(),)* }
            }
        }

        $(impl StateDomain for $ty {
            const NAME: &'static str = stringify!($field);
            fn cell(states: &States) -> &watch::Sender<Self> { &states.$field }
            fn into_update(self) -> StateUpdate { StateUpdate::$variant(self) }
        })*
    };
}

domains! {
    stage: StageState => Stage,
    conversation: ConversationState => Conversation,
    engagement: EngagementState => Engagement,
    music: MusicState => Music,
    dj: DjState => Dj,
    perf: PerfState => Perf,
    lights: LightsState => Lights,
    services: ServicesState => Services,
}
