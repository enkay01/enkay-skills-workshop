use byteorder::{LittleEndian, ReadBytesExt, WriteBytesExt};
use serde::{Deserialize, Serialize};
use std::io::{Read, Write};

pub const PROTOCOL_VERSION: u32 = 1;
pub const MAX_HEADER_LEN: u32 = 65536; // 64 KiB
pub const MAX_PAYLOAD_LEN: u32 = 134217728; // 128 MiB

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Request {
    pub v: u32,
    pub id: u64,
    pub op: String,
    #[serde(default)]
    pub args: serde_json::Value,
    #[serde(default)]
    pub payload_len: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Response {
    pub v: u32,
    pub id: u64,
    pub ok: bool,
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub result: serde_json::Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<ProtocolError>,
    #[serde(default)]
    pub payload_len: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProtocolError {
    pub code: String,
    pub message: String,
    #[serde(default, skip_serializing_if = "serde_json::Value::is_null")]
    pub details: serde_json::Value,
}

impl ProtocolError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            details: serde_json::Value::Null,
        }
    }

    #[allow(dead_code)]
    pub fn with_details(code: impl Into<String>, message: impl Into<String>, details: serde_json::Value) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            details,
        }
    }
}

pub fn read_request<R: Read>(reader: &mut R) -> Result<Option<(Request, Vec<u8>)>, ProtocolError> {
    let header_len = match reader.read_u32::<LittleEndian>() {
        Ok(len) => len,
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(ProtocolError::new("transport_error", format!("Failed reading header length: {}", e))),
    };

    if header_len > MAX_HEADER_LEN {
        return Err(ProtocolError::new(
            "invalid_request",
            format!("Header length {} exceeds limit of {} bytes", header_len, MAX_HEADER_LEN),
        ));
    }

    let mut header_buf = vec![0u8; header_len as usize];
    if let Err(e) = reader.read_exact(&mut header_buf) {
        return Err(ProtocolError::new("invalid_request", format!("Truncated header: {}", e)));
    }

    let header_str = match std::str::from_utf8(&header_buf) {
        Ok(s) => s,
        Err(e) => return Err(ProtocolError::new("invalid_request", format!("Header is not valid UTF-8: {}", e))),
    };

    let req: Request = match serde_json::from_str(header_str) {
        Ok(r) => r,
        Err(e) => return Err(ProtocolError::new("invalid_request", format!("Invalid JSON header: {}", e))),
    };

    if req.v != PROTOCOL_VERSION {
        return Err(ProtocolError::new(
            "unsupported",
            format!("Unsupported protocol version: got {}, expected {}", req.v, PROTOCOL_VERSION),
        ));
    }

    if req.payload_len > MAX_PAYLOAD_LEN {
        return Err(ProtocolError::new(
            "invalid_request",
            format!("Payload length {} exceeds limit of {} bytes", req.payload_len, MAX_PAYLOAD_LEN),
        ));
    }

    let mut payload = vec![0u8; req.payload_len as usize];
    if req.payload_len > 0 {
        if let Err(e) = reader.read_exact(&mut payload) {
            return Err(ProtocolError::new("invalid_request", format!("Truncated payload: {}", e)));
        }
    }

    Ok(Some((req, payload)))
}

pub fn write_response<W: Write>(writer: &mut W, resp: &Response, payload: &[u8]) -> std::io::Result<()> {
    let header_json = serde_json::to_vec(resp)?;
    let header_len = header_json.len() as u32;

    writer.write_u32::<LittleEndian>(header_len)?;
    writer.write_all(&header_json)?;
    if !payload.is_empty() {
        writer.write_all(payload)?;
    }
    writer.flush()?;
    Ok(())
}
