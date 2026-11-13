//! Pure: what an error usually means, keyed by exception type or message. Given to the model as
//! background ("what this error usually means"), never as the answer and never as evidence. Unknown
//! errors get no hint.

use std::sync::LazyLock;

use regex::Regex;

use crate::util::re;

const HINTS: &[(&str, &str)] = &[
    (
        r"RecursionError|StackOverflowError|maximum call stack size exceeded|stack overflow",
        concat!(
            "A function calls itself (directly or through others) without reaching a base case. Usual causes: a missing or ",
            "unreachable base case; two objects referring to each other (e.g. in serialisation); a property getter that ",
            "reads itself."
        ),
    ),
    (
        r"AttributeError: 'NoneType'|NoneType' object",
        concat!(
            "An earlier call returned None and the code used it as an object. Usual causes: a lookup that found nothing; ",
            "a function that forgot to return; an optional field that is unset for some records."
        ),
    ),
    (
        r"KeyError",
        concat!(
            "A dict was read with a key it does not have. Usual causes: a misspelt key; the producer does ",
            "not always set it; reading user input that omits it (use .get or validate first)."
        ),
    ),
    (
        r"ModuleNotFoundError|Cannot find module|No module named",
        concat!(
            "The package or module is not importable here. Usual causes: not installed in this environment or ",
            "virtualenv/image; a wrong import path; a file renamed or moved."
        ),
    ),
    (
        r"ImportError: cannot import name",
        concat!(
            "The module exists but the name is not in it. Usual causes: a circular import; the symbol was renamed or ",
            "removed in another version; importing from the wrong module."
        ),
    ),
    (
        r"is not JSON serializable|Converting circular structure to JSON",
        concat!(
            "An object the JSON encoder cannot handle was serialised. Usual causes: a datetime/Decimal/set/custom object ",
            "in the payload; a circular reference; a model object passed instead of a dict."
        ),
    ),
    (
        r"ECONNREFUSED|[Cc]onnection refused",
        concat!(
            "Nothing is listening on that host:port. Usual causes: the service is down or still starting; the wrong host or ",
            "port; container networking (localhost inside a container is the container itself)."
        ),
    ),
    (
        r"ETIMEDOUT|timed out|TimeoutError|SocketTimeoutException|deadline exceeded",
        concat!(
            "The other side did not answer in time. Usual causes: the host is unreachable (firewall, wrong address); the ",
            "service is overloaded or blocked; the timeout is shorter than the work takes."
        ),
    ),
    (
        r"UnicodeDecodeError|UnicodeEncodeError",
        concat!(
            "Bytes were decoded with the wrong encoding. Usual causes: a file that is not UTF-8; binary data read as text; ",
            "a default locale encoding that differs between machines."
        ),
    ),
    (
        r"IndexError|ArrayIndexOutOfBounds|IndexOutOfRange|index out of range|out of bounds",
        concat!(
            "A list or array was read past its end. Usual causes: an empty list; an off-by-one loop bound; assuming a ",
            "fixed number of items from input."
        ),
    ),
    (
        r"ZeroDivisionError|divide by zero|DivideByZeroException",
        concat!(
            "A division had a zero divisor. Usual causes: an empty collection's count or total; an unset value defaulting ",
            "to 0; a rounding to 0."
        ),
    ),
    (
        r"NullPointerException|NullReferenceException",
        concat!(
            "A null reference was used as an object. Usual causes: a lookup or injection that returned null; an unset field; ",
            "a collection element that is null. Java 14+ messages name the null expression: quote it."
        ),
    ),
    (
        r"ClassNotFoundException|NoClassDefFoundError|NoSuchMethodError",
        concat!(
            "The classpath does not match what the code was compiled against. Usual causes: a missing dependency; two ",
            "versions of a library; a stale build artifact."
        ),
    ),
    (
        r"OutOfMemoryError|out of memory|JavaScript heap out of memory|MemoryError",
        concat!(
            "The process ran out of memory. Usual causes: loading a whole large file or result set at once; a leak (a ",
            "cache or list that only grows); a heap limit set too low."
        ),
    ),
    (
        r"ConcurrentModificationException|changed size during iteration",
        concat!(
            "A collection was modified while being iterated. Usual causes: removing items inside a for-each loop; another ",
            "thread changing it; iterate over a copy or collect changes first."
        ),
    ),
    (
        r"nil pointer dereference|invalid memory address",
        concat!(
            "A nil pointer was dereferenced. Usual causes: a constructor or lookup returned nil with an error that was not ",
            "checked; a struct field never initialised; a nil map or interface."
        ),
    ),
    (
        r"all goroutines are asleep - deadlock",
        concat!(
            "Every goroutine is blocked. Usual causes: sending on an unbuffered channel with no receiver; waiting on a ",
            "WaitGroup that is never Done; a mutex locked twice."
        ),
    ),
    (
        r"called `?Option::unwrap\(\)`? on a `?None`? value|called `?Result::unwrap\(\)`? on an `?Err`?",
        concat!(
            "unwrap() met a None/Err. Usual causes: a missing key, file or env var; input that did not parse; handle it ",
            "with match/? or expect with context."
        ),
    ),
    (
        r"BorrowMutError|already borrowed|BorrowError",
        concat!(
            "A RefCell was borrowed mutably while another borrow was alive. Usual causes: a borrow held across a call that ",
            "borrows again; a borrow kept in a variable longer than needed."
        ),
    ),
    (
        r"Cannot read propert(?:y|ies) of (?:undefined|null)|undefined is not an object",
        concat!(
            "A property was read from undefined or null. Usual causes: an async value used before it resolved; a missing ",
            "field in API data; a wrong variable or a typo in the path."
        ),
    ),
    (
        r"is not a function",
        concat!(
            "Something called as a function is not one. Usual causes: a wrong import (default vs ",
            "named); a method on the wrong object; a property name typo."
        ),
    ),
    (
        r"UnhandledPromiseRejection|Unhandled promise rejection",
        concat!(
            "A promise rejected with no handler. Usual causes: a missing await or .catch; an error thrown inside an async ",
            "callback; the real error is the rejection reason."
        ),
    ),
    (
        r"ENOENT|No such file or directory|FileNotFoundError",
        concat!(
            "A file or directory does not exist at that path. Usual causes: a relative path resolved from a different ",
            "working directory; a file not copied into the image; a typo in the name."
        ),
    ),
    (
        r"EACCES|PermissionError|[Pp]ermission denied|EPERM",
        concat!(
            "The process lacks permission. Usual causes: a non-root user writing to a root-owned path; file mode bits; a ",
            "port below 1024."
        ),
    ),
    (
        r"violates foreign key constraint|foreign key constraint fails",
        concat!(
            "A row references a parent row that does not exist (or a parent was deleted while children remain). Usual ",
            "causes: inserting in the wrong order; a stale id; a missing ON DELETE rule."
        ),
    ),
    (
        r"deadlock detected|Deadlock found",
        concat!(
            "Two transactions waited on each other's locks. Usual causes: locking rows in different orders; long ",
            "transactions; retry the transaction and lock in a consistent order."
        ),
    ),
    (
        r#"relation "[^"]+" does not exist|no such table|Table '[^']+' doesn't exist"#,
        concat!(
            "The table is missing in this database. Usual causes: migrations not applied; the wrong database or schema; ",
            "a quoted, case-sensitive name."
        ),
    ),
    (
        r"TypeError",
        concat!(
            "A value of the wrong type was used. Usual causes: None/undefined where a value was expected; a ",
            "string where a number was expected; wrong arguments to a call."
        ),
    ),
    (
        r"ValueError|NumberFormatException|ParseIntError|invalid digit",
        concat!(
            "A value had the right type but an unusable content. Usual causes: parsing text that is empty or malformed; an ",
            "unexpected unit or format in input."
        ),
    ),
    (
        r"AssertionError|assertion failed",
        concat!(
            "An assertion's condition was false. Usual causes: a test expectation that ",
            "no longer matches; an invariant broken earlier; compare the expected and actual values in the message."
        ),
    ),
    (
        r"SSLError|certificate verify failed|CERT_",
        concat!(
            "TLS verification failed. Usual causes: a self-signed or expired ",
            "certificate; a missing CA bundle in the image; a proxy intercepting TLS."
        ),
    ),
];

static COMPILED: LazyLock<Vec<(Regex, &'static str)>> =
    LazyLock::new(|| HINTS.iter().map(|(p, t)| (re(p), *t)).collect());

/// The first hint whose pattern matches `Type: message`, or "" when none does.
pub fn hint_for(exception_type: &str, message: &str) -> &'static str {
    let headline = if message.is_empty() {
        exception_type.to_string()
    } else {
        format!("{exception_type}: {message}")
    };
    COMPILED
        .iter()
        .find(|(pattern, _)| pattern.is_match(&headline))
        .map_or("", |(_, text)| text)
}
