//! Pure: transcript clock times, as seconds and as shown.

/// Hours or minutes beyond this are not a clock time (keeps the arithmetic far from overflow).
const MAX_PART: i64 = 1_000_000;

/// `12:03` -> 723, `1:02:03.500` -> 3723; -1 when it is not a time.
pub fn to_secs(value: &str) -> i64 {
    let clock = value.trim().split(['.', ',']).next().unwrap_or("");
    let parts: Vec<&str> = clock.split(':').collect();
    if !(2..=3).contains(&parts.len())
        || !parts
            .iter()
            .all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
    {
        return -1;
    }
    let mut secs: i64 = 0;
    for part in parts {
        let n = part.parse::<i64>().unwrap_or(MAX_PART).min(MAX_PART);
        secs = secs * 60 + n;
    }
    secs
}

pub fn show_time(secs: i64) -> String {
    if secs < 0 {
        return String::new();
    }
    let (hours, rest) = (secs / 3600, secs % 3600);
    if hours > 0 {
        format!("{hours}:{:02}:{:02}", rest / 60, rest % 60)
    } else {
        format!("{:02}:{:02}", rest / 60, rest % 60)
    }
}
