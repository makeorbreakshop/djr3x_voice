//! Visitor tokens: short-lived, HMAC-SHA256 signed, stateless.
//!
//! `v1.<exp unix s>.<visitor id>.<hex hmac(secret, "v1.<exp>.<id>")>`. The id keys the
//! per-visitor budget, so reconnecting with the same token shares it. Minted by `POST /token`
//! (admin bearer) or [`mint`] directly by whatever embeds the page.

use sha2::{Digest, Sha256};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Visitor {
    pub id: String,
    /// Expiry, unix seconds.
    pub exp: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TokenError {
    Malformed,
    BadSignature,
    Expired,
}

pub fn now_unix() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

pub fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    let mut k = [0u8; 64];
    if key.len() > 64 {
        k[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        k[..key.len()].copy_from_slice(key);
    }
    let pad = |b: u8| k.map(|x| x ^ b);
    let inner = Sha256::new().chain_update(pad(0x36)).chain_update(msg).finalize();
    Sha256::new().chain_update(pad(0x5c)).chain_update(inner).finalize().into()
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn sign(secret: &[u8], exp: u64, id: &str) -> String {
    hex(&hmac_sha256(secret, format!("v1.{exp}.{id}").as_bytes()))
}

/// A token for a new visitor, valid until `exp` (unix s).
pub fn mint(secret: &[u8], exp: u64) -> (String, Visitor) {
    let id = uuid::Uuid::new_v4().simple().to_string();
    (format!("v1.{exp}.{id}.{}", sign(secret, exp, &id)), Visitor { id, exp })
}

pub fn verify(secret: &[u8], token: &str, now: u64) -> Result<Visitor, TokenError> {
    let mut parts = token.split('.');
    let (Some("v1"), Some(exp), Some(id), Some(sig), None) = (parts.next(), parts.next(), parts.next(), parts.next(), parts.next()) else {
        return Err(TokenError::Malformed);
    };
    let exp: u64 = exp.parse().map_err(|_| TokenError::Malformed)?;
    if id.is_empty() || !id.bytes().all(|c| c.is_ascii_alphanumeric()) {
        return Err(TokenError::Malformed);
    }
    let want = sign(secret, exp, id);
    let same = want.len() == sig.len() && want.bytes().zip(sig.bytes()).fold(0u8, |a, (x, y)| a | (x ^ y)) == 0;
    if !same {
        return Err(TokenError::BadSignature);
    }
    if exp <= now {
        return Err(TokenError::Expired);
    }
    Ok(Visitor { id: id.to_owned(), exp })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hmac_matches_rfc4231_case_2() {
        let mac = hmac_sha256(b"Jefe", b"what do ya want for nothing?");
        assert_eq!(hex(&mac), "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
    }

    #[test]
    fn mint_verify_tamper_expire() {
        let (s, now) = (b"secret".as_slice(), 1_000);
        let (tok, v) = mint(s, now + 60);
        assert_eq!(verify(s, &tok, now), Ok(v.clone()));
        assert_eq!(verify(b"other", &tok, now), Err(TokenError::BadSignature));
        let forged = tok.replacen(&format!("v1.{}", now + 60), &format!("v1.{}", now + 9999), 1);
        assert_eq!(verify(s, &forged, now), Err(TokenError::BadSignature));
        assert_eq!(verify(s, &tok, now + 60), Err(TokenError::Expired));
        assert_eq!(verify(s, "v1.x.y", now), Err(TokenError::Malformed));
    }
}
