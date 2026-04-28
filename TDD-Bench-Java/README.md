To build TDD-bench from source, follow these steps:
```bash
bash setup.sh
```


Evaluating golden test patch:
```bash
bash command_run_gold.sh
```


Evaluating a test patch from file:
```bash
python3 src/poly_bench_evaluation/run_evaluation.py --dataset-path java.csv --result-path ./eval_logs/ --num-threads 1 --repo-path ./repos --delete-image --instance_id_to_run google__gson-2337 --predictions-path version_5_gpt4o.json --skip-existing
```

Sample example from prediction file
```bash
    [{
        "instance_id": "google__gson-2337",
        "model_patch": "diff --git a/gson/src/test/java/com/google/gson/stream/JsonReaderTest.java b/gson/src/test/java/com/google/gson/stream/JsonReaderTest.java\nindex 8ebe20b5..22cfd0ce 100644\n--- a/gson/src/test/java/com/google/gson/stream/JsonReaderTest.java\n+++ b/gson/src/test/java/com/google/gson/stream/JsonReaderTest.java\n@@ -16,6 +16,7 @@\n \n package com.google.gson.stream;\n \n+\n import static com.google.gson.stream.JsonToken.BEGIN_ARRAY;\n import static com.google.gson.stream.JsonToken.BEGIN_OBJECT;\n import static com.google.gson.stream.JsonToken.BOOLEAN;\n@@ -359,6 +360,20 @@ public final class JsonReaderTest {\n     }\n   }\n \n+  @Test\n+  public void testMalformedUnicodeEscapeSequence() throws IOException {\n+    String json = \"\\\"\\\\uXYZ\\\"\";\n+    JsonReader reader = new JsonReader(new StringReader(json));\n+    try {\n+      reader.nextString();\n+      fail();\n+    } catch (MalformedJsonException expected) {\n+      // Expected exception for malformed Unicode escape sequence\n+    }\n+  }\n+  \n+\n+\n   @Test\n   public void testUnescapingTruncatedCharacters() throws IOException {\n     String json = \"[\\\"\\\\u000\";\n@@ -2039,3 +2054,5 @@ public final class JsonReaderTest {\n     }; */\n   }\n }\n+\n+\n",
        "class_name": "JsonReaderTest",
        "file_name": "gson/src/test/java/com/google/gson/stream/JsonReaderTest.java",
        "function_name": "testMalformedUnicodeEscapeSequence",
        "model_name_or_path": "PH"
    }]
```

Note that it will work with empty "class_name", "file_name", and "function_name".  
