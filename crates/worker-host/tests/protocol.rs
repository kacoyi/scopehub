use scopehub_worker_host::{SCHEMA, decode_line, max_message_bytes};
use serde_json::Value;

#[test]
fn shared_vectors_and_generated_types_agree() {
    let examples: Vec<Value> =
        serde_json::from_str(include_str!("../../../tests/fixtures/protocol-cases.json")).unwrap();
    for example in examples {
        let result = decode_line(example["wire"].as_str().unwrap().as_bytes());
        assert_eq!(
            result.is_ok(),
            example["valid"].as_bool().unwrap(),
            "{}: {:?}",
            example["name"],
            result
        );
        if let Ok(message) = result {
            let encoded = serde_json::to_vec(&message).unwrap();
            assert!(
                decode_line(&encoded).is_ok(),
                "roundtrip {}",
                example["name"]
            );
        }
    }
}

#[test]
fn rejects_invalid_utf8_and_oversize_frames() {
    assert!(decode_line(&[0xff, b'\n']).is_err());
    assert!(decode_line(&vec![b'x'; max_message_bytes() + 1]).is_err());
}

#[test]
fn every_operation_definition_compiles() {
    let manifest: Value =
        serde_json::from_str(include_str!("../../../contracts/contract.json")).unwrap();
    for operation in manifest["operations"].as_array().unwrap() {
        for direction in ["input", "output"] {
            let mut schema = SCHEMA.clone();
            schema["$ref"] = Value::String(format!(
                "#/$defs/{}",
                operation[direction].as_str().unwrap()
            ));
            jsonschema::options()
                .should_validate_formats(true)
                .build(&schema)
                .unwrap();
        }
    }
}
