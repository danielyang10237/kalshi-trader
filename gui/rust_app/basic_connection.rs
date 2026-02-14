use std::{path::Path, time::Duration};

use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use dotenvy::dotenv;
use futures_util::{SinkExt, StreamExt};
use http::Request;
use rand::rngs::OsRng;
use rsa::{
    pkcs1::DecodeRsaPrivateKey,
    pkcs8::DecodePrivateKey,
    pss::SigningKey,
    RsaPrivateKey,
};
use serde_json::json;
use sha2::Sha256;
use signature::Signer;
use tokio::time::sleep;
use tokio_tungstenite::{connect_async, tungstenite::Message};

const PROD_WS_URL: &str = "wss://api.elections.kalshi.com/trade-api/ws/v2";
const DEMO_WS_URL: &str = "wss://demo-api.kalshi.co/trade-api/ws/v2";
const WS_PATH: &str = "/trade-api/ws/v2";
const DEFAULT_PRIVATE_KEY_PATH: &str = "kalshi_key.pem";

#[derive(Clone, Debug)]
pub struct KalshiWsConfig {
    pub ws_url: String,
    pub key_id: String,
    pub private_key_path: String,
}

pub fn load_config_from_env() -> Result<KalshiWsConfig> {
    dotenv().ok();

    let key_id = std::env::var("KALSHI_API_KEY")
        .context("Missing KALSHI_API_KEY in .env (Kalshi API key ID)")?;

    let private_key_path =
        std::env::var("KALSHI_PRIVATE_KEY_PATH").unwrap_or_else(|_| DEFAULT_PRIVATE_KEY_PATH.to_string());

    let ws_url = std::env::var("KALSHI_WS_URL").unwrap_or_else(|_| DEMO_WS_URL.to_string());

    Ok(KalshiWsConfig {
        ws_url,
        key_id,
        private_key_path,
    })
}

pub async fn run_forever(
    config: KalshiWsConfig,
    channels: Vec<String>,
    market_tickers: Vec<String>,
) -> Result<()> {
    let private_key = load_rsa_private_key(&config.private_key_path)?;

    let mut attempt: u32 = 0;
    loop {
        attempt = attempt.saturating_add(1);
        let backoff_ms = (250u64 * attempt as u64).min(10_000);

        match connect_once(&config, &private_key, &channels, &market_tickers).await {
            Ok(()) => {
                attempt = 0;
                eprintln!("[kalshi_ws] Disconnected. Reconnecting...");
            }
            Err(e) => {
                eprintln!("[kalshi_ws] Error: {e:#}");
                eprintln!("[kalshi_ws] Reconnecting in {backoff_ms}ms...");
                sleep(Duration::from_millis(backoff_ms)).await;
            }
        }
    }
}

async fn connect_once(
    config: &KalshiWsConfig,
    private_key: &RsaPrivateKey,
    channels: &[String],
    market_tickers: &[String],
) -> Result<()> {
    let timestamp_ms = unix_ms_string();
    let to_sign = format!("{timestamp_ms}GET{WS_PATH}");
    let signature_b64 = sign_pss_sha256_base64(private_key, to_sign.as_bytes())?;

    let req = Request::builder()
        .method("GET")
        .uri(&config.ws_url)
        .header("KALSHI-ACCESS-KEY", &config.key_id)
        .header("KALSHI-ACCESS-SIGNATURE", signature_b64)
        .header("KALSHI-ACCESS-TIMESTAMP", timestamp_ms)
        .header("Content-Type", "application/json")
        .body(())
        .map_err(|e| anyhow!("Failed building WS request: {e}"))?;

    eprintln!("[kalshi_ws] Connecting to {} ...", config.ws_url);
    let (ws_stream, resp) = connect_async(req).await?;
    eprintln!("[kalshi_ws] Connected. HTTP status = {}", resp.status());

    let (mut write, mut read) = ws_stream.split();

    // Subscribe
    let mut params = json!({ "channels": channels });
    if !market_tickers.is_empty() {
        params["market_tickers"] = json!(market_tickers);
    }

    let subscribe_msg = json!({
        "id": 1,
        "cmd": "subscribe",
        "params": params
    });

    write.send(Message::Text(subscribe_msg.to_string())).await?;
    eprintln!(
        "[kalshi_ws] Subscribed. channels={:?}, market_tickers={:?}",
        channels, market_tickers
    );

    while let Some(item) = read.next().await {
        match item {
            Ok(Message::Text(txt)) => {
                println!("[kalshi_ws] TEXT: {txt}");
            }
            Ok(Message::Binary(bin)) => {
                println!("[kalshi_ws] BINARY ({} bytes)", bin.len());
            }
            Ok(Message::Ping(p)) => {
                write.send(Message::Pong(p)).await?;
            }
            Ok(Message::Pong(_)) => {}
            Ok(Message::Close(frame)) => {
                eprintln!("[kalshi_ws] CLOSE: {frame:?}");
                break;
            }
            Ok(other) => {
                eprintln!("[kalshi_ws] WS: {other:?}");
            }
            Err(e) => return Err(anyhow!("WebSocket read error: {e}")),
        }
    }

    Ok(())
}

fn load_rsa_private_key(p: &str) -> Result<RsaPrivateKey> {
    let pem = std::fs::read_to_string(Path::new(p))
        .with_context(|| format!("Failed reading private key PEM at: {p}"))?;

    if let Ok(k) = RsaPrivateKey::from_pkcs8_pem(&pem) {
        return Ok(k);
    }
    if let Ok(k) = RsaPrivateKey::from_pkcs1_pem(&pem) {
        return Ok(k);
    }

    Err(anyhow!(
        "Could not parse RSA private key. Expected PKCS#8 or PKCS#1 PEM at: {p}"
    ))
}

fn sign_pss_sha256_base64(private_key: &RsaPrivateKey, message: &[u8]) -> Result<String> {
    let signing_key = SigningKey::<Sha256>::new(private_key.clone());
    let mut rng = OsRng;

    let sig = signing_key
        .sign_with_rng(&mut rng, message)
        .to_bytes()
        .to_vec();

    Ok(STANDARD.encode(sig))
}

fn unix_ms_string() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("time went backwards")
        .as_millis();
    ms.to_string()
}

#[allow(dead_code)]
pub fn demo_url() -> &'static str {
    DEMO_WS_URL
}
#[allow(dead_code)]
pub fn prod_url() -> &'static str {
    PROD_WS_URL
}