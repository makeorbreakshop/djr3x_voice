//! The local library: scan, artist/title from the filename, duplicate-title keys, and the
//! named-track matching CantinaOS's `MusicControllerService` does (plan §7a).

use std::path::{Path, PathBuf};

use serde::Serialize;

/// Artist for files named without `Artist - `.
pub const DEFAULT_ARTIST: &str = "Cantina Band";

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LibTrack {
    /// Unique library key (the title, or `Artist - Title` when titles collide). Every command
    /// addresses tracks by this, as CantinaOS does.
    pub key: String,
    pub title: String,
    pub artist: String,
    pub path: PathBuf,
    pub duration_s: Option<f64>,
    pub bpm: Option<f64>,
    pub first_beat_s: Option<f64>,
}

impl LibTrack {
    pub fn to_contract(&self) -> r3x_contracts::Track {
        r3x_contracts::Track {
            title: self.key.clone(),
            artist: Some(self.artist.clone()),
            path: Some(self.path.to_string_lossy().into_owned()),
            duration_s: self.duration_s,
            bpm: self.bpm,
            first_beat_s: self.first_beat_s,
        }
    }
}

/// `MUSIC_DIR`, else `audio/music` at the repo root, else `cantina_os/assets/music` (CantinaOS
/// `main.py`).
pub fn default_music_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("MUSIC_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(d);
    }
    let root = r3x_beats::abspath(Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../../..")));
    let primary = root.join("audio/music");
    if primary.is_dir() {
        primary
    } else {
        root.join("cantina_os/assets/music")
    }
}

/// `"Artist - Title.mp3"` -> (artist, title); no separator -> ("Cantina Band", name).
pub fn parse_filename(filename: &str) -> (String, String) {
    let stem = Path::new(filename).file_stem().and_then(|s| s.to_str()).unwrap_or(filename);
    match stem.split_once(" - ") {
        Some((a, t)) => (a.trim().to_owned(), t.trim().to_owned()),
        None => (DEFAULT_ARTIST.to_owned(), stem.trim().to_owned()),
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Library {
    /// In CantinaOS's dict order (track numbers and `next` walk it).
    pub tracks: Vec<LibTrack>,
}

impl Library {
    /// Scan `dir` for `.mp3`, then `.wav`, then `.m4a` (each sorted), exactly as CantinaOS's
    /// glob loop. `duration` probes each file (blocking).
    pub fn scan(dir: &Path, probe: bool) -> Library {
        let mut lib = Library::default();
        let mut entries: Vec<PathBuf> = match std::fs::read_dir(dir) {
            Ok(rd) => rd.filter_map(|e| e.ok().map(|e| e.path())).collect(),
            Err(e) => {
                tracing::warn!("music directory {}: {e}", dir.display());
                return lib;
            }
        };
        entries.sort();
        for ext in r3x_audio::decode::MUSIC_EXTENSIONS {
            for path in entries.iter().filter(|p| {
                let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
                !name.starts_with('.') && p.extension().and_then(|e| e.to_str()) == Some(ext) && p.is_file()
            }) {
                let filename = path.file_name().unwrap().to_string_lossy();
                let (artist, title) = parse_filename(&filename);
                let duration_s = if probe {
                    match r3x_audio::decode::probe(path) {
                        Ok(i) => i.duration_s,
                        Err(e) => {
                            tracing::warn!("could not read {}: {e}", path.display());
                            None
                        }
                    }
                } else {
                    None
                };
                let path = r3x_beats::abspath(path);
                lib.insert(LibTrack { key: title.clone(), title, artist, path, duration_s, bpm: None, first_beat_s: None });
            }
        }
        lib
    }

    /// Insert with CantinaOS's duplicate-title rule: the plain title stays with the generic
    /// "Cantina Band" file; the other recording gets `Artist - Title` (then ` (2)`, ...).
    pub fn insert(&mut self, mut t: LibTrack) {
        let title = t.title.clone();
        let mut key = title.clone();
        if let Some(i) = self.index_of(&title) {
            if t.artist == DEFAULT_ARTIST && self.tracks[i].artist != DEFAULT_ARTIST {
                let mut existing = self.tracks.remove(i); // re-inserted at the end, as dict del+set
                existing.key = self.unique(format!("{} - {title}", existing.artist));
                tracing::info!("disambiguated duplicate track title {title:?} as {:?}", existing.key);
                self.tracks.push(existing);
            } else {
                key = self.unique(format!("{} - {title}", t.artist));
                tracing::info!("disambiguated duplicate track title {title:?} as {key:?}");
            }
        }
        t.key = key;
        self.tracks.push(t);
    }

    fn unique(&self, base: String) -> String {
        let mut k = base.clone();
        let mut n = 2;
        while self.index_of(&k).is_some() {
            k = format!("{base} ({n})");
            n += 1;
        }
        k
    }

    pub fn index_of(&self, key: &str) -> Option<usize> {
        self.tracks.iter().position(|t| t.key == key)
    }

    pub fn get(&self, key: &str) -> Option<&LibTrack> {
        self.tracks.iter().find(|t| t.key == key)
    }

    pub fn keys(&self) -> Vec<String> {
        self.tracks.iter().map(|t| t.key.clone()).collect()
    }

    pub fn is_empty(&self) -> bool {
        self.tracks.is_empty()
    }

    pub fn len(&self) -> usize {
        self.tracks.len()
    }

    /// `_find_named_track`: a confident title/artist match, never a musical-semantics guess.
    pub fn find_named(&self, query: &str) -> Option<&LibTrack> {
        let q = words(query);
        if q.is_empty() {
            return None;
        }
        let nq = q.join(" ");
        let mut best: Option<(&LibTrack, f64)> = None;
        for t in &self.tracks {
            for cand in [&t.key, &t.title, &t.artist] {
                let cw = words(cand);
                if cw.is_empty() {
                    continue;
                }
                let nc = cw.join(" ");
                let score = if nc.contains(&nq) {
                    1.0 + nq.chars().count() as f64 / nc.chars().count().max(1) as f64
                } else if q.len() == 1 && q[0].chars().count() >= 4 {
                    let s = cw.iter().map(|w| ratio(&q[0], w)).fold(0.0, f64::max);
                    if s < 0.66 {
                        continue;
                    }
                    s
                } else {
                    let s = ratio(&nq, &nc);
                    if s < 0.72 {
                        continue;
                    }
                    s
                };
                if score > best.map_or(0.0, |b| b.1) {
                    best = Some((t, score));
                }
            }
        }
        best.map(|b| b.0)
    }

    /// `_play_track_by_name`: exact key, else the best case-insensitive substring match.
    pub fn by_name(&self, name: &str) -> Option<&LibTrack> {
        if let Some(t) = self.get(name) {
            return Some(t);
        }
        let n = name.to_lowercase();
        let mut best: Option<(&LibTrack, f64)> = None;
        for t in &self.tracks {
            if t.key.to_lowercase().contains(&n) {
                let s = name.chars().count() as f64 / t.key.chars().count() as f64;
                if s > best.map_or(0.0, |b| b.1) {
                    best = Some((t, s));
                }
            }
        }
        best.map(|b| b.0)
    }

    /// `list music` reply.
    pub fn listing(&self) -> String {
        let lines: Vec<String> = self.tracks.iter().enumerate().map(|(i, t)| format!("{}. {}", i + 1, t.key)).collect();
        format!("Available tracks:\n{}", lines.join("\n"))
    }
}

/// `re.findall(r"[a-z0-9]+", value.lower())`
pub fn words(s: &str) -> Vec<String> {
    let lower = s.to_lowercase();
    let mut out = Vec::new();
    let mut cur = String::new();
    for c in lower.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            cur.push(c);
        } else if !cur.is_empty() {
            out.push(std::mem::take(&mut cur));
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// `difflib.SequenceMatcher(None, a, b).ratio()` (no junk; inputs are short).
pub fn ratio(a: &str, b: &str) -> f64 {
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();
    if a.len() + b.len() == 0 {
        return 1.0;
    }
    let mut b2j: std::collections::HashMap<char, Vec<usize>> = Default::default();
    for (j, c) in b.iter().enumerate() {
        b2j.entry(*c).or_default().push(j);
    }
    let longest = |alo: usize, ahi: usize, blo: usize, bhi: usize| -> (usize, usize, usize) {
        let (mut bi, mut bj, mut bk) = (alo, blo, 0usize);
        let mut j2len: std::collections::HashMap<usize, usize> = Default::default();
        for (i, ai) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut new: std::collections::HashMap<usize, usize> = Default::default();
            for &j in b2j.get(ai).map(Vec::as_slice).unwrap_or(&[]) {
                if j < blo {
                    continue;
                }
                if j >= bhi {
                    break;
                }
                let k = j.checked_sub(1).and_then(|p| j2len.get(&p)).copied().unwrap_or(0) + 1;
                new.insert(j, k);
                if k > bk {
                    (bi, bj, bk) = (i + 1 - k, j + 1 - k, k);
                }
            }
            j2len = new;
        }
        (bi, bj, bk)
    };
    let mut matched = 0;
    let mut queue = vec![(0, a.len(), 0, b.len())];
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = longest(alo, ahi, blo, bhi);
        if k > 0 {
            matched += k;
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    2.0 * matched as f64 / (a.len() + b.len()) as f64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(file: &str) -> LibTrack {
        let (artist, title) = parse_filename(file);
        LibTrack { key: title.clone(), title, artist, path: file.into(), duration_s: None, bpm: None, first_beat_s: None }
    }

    /// The real library's file names, in glob order, give CantinaOS's keys and order.
    #[test]
    fn keys_and_order_match_cantina() {
        let mut lib = Library::default();
        for f in [
            "Bai Tee Tee.mp3",
            "Duro Droids - Beep Boop Bop.mp3",
            "Gaya - Oola Shuka.mp3",
            "Gaya.mp3",
            "The Dusty Jawas - Utinni.mp3",
            "Utinni.mp3",
            "Vee Gooda, Ryco - Aloogahoo.mp3",
        ] {
            lib.insert(t(f));
        }
        // (CantinaOS's index order: ..., "The Dusty Jawas - Utinni", "Utinni", "Aloogahoo", ...)
        assert_eq!(lib.keys(), ["Bai Tee Tee", "Beep Boop Bop", "Oola Shuka", "Gaya", "The Dusty Jawas - Utinni", "Utinni", "Aloogahoo"]);
        // The generic one keeps the plain title; the moved one went to the end, as dict del+set.
        lib.insert(t("Other - Utinni.mp3"));
        assert_eq!(lib.tracks.last().unwrap().key, "Other - Utinni");
        lib.insert(t("Other - Utinni.wav"));
        assert_eq!(lib.tracks.last().unwrap().key, "Other - Utinni (2)");
        assert_eq!(lib.get("Beep Boop Bop").unwrap().artist, "Duro Droids");
    }

    #[test]
    fn named_matching_like_python() {
        // difflib reference values (python3 -c 'SequenceMatcher(None,a,b).ratio()')
        assert!((ratio("java", "jawas") - 0.6666666666666666).abs() < 1e-12);
        assert!((ratio("bai tee tee", "bai tea tee") - 0.9090909090909091).abs() < 1e-12);
        assert!((ratio("abcd", "bcda") - 0.75).abs() < 1e-12);
        let mut lib = Library::default();
        for f in ["Bai Tee Tee.mp3", "The Dusty Jawas - Utinni.mp3", "Mus Kat & Nalpak - Doshka.mp3", "Utinni.mp3"] {
            lib.insert(t(f));
        }
        assert_eq!(lib.find_named("doshka").unwrap().key, "Doshka");
        assert_eq!(lib.find_named("java").unwrap().key, "The Dusty Jawas - Utinni", "STT slip");
        assert_eq!(lib.find_named("bai tea tee").unwrap().key, "Bai Tee Tee");
        assert!(lib.find_named("something upbeat").is_none());
        assert_eq!(lib.by_name("utin").unwrap().key, "Utinni");
        assert!(lib.listing().starts_with("Available tracks:\n1. Bai Tee Tee\n"));
    }
}
