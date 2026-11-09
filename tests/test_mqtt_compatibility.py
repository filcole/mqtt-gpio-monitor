import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import paho.mqtt.client as mqtt


SCRIPT = Path(__file__).resolve().parents[1] / "mqtt-gpio-monitor.py"


def load_mqtt_setup(mqtt_module, client_id="gpio-monitor", clean_session=False):
    tree = ast.parse(SCRIPT.read_text())
    # Isolate MQTT setup and callbacks from hardware setup and the polling loop.
    start = next(
        index for index, node in enumerate(tree.body)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "MQTT_CLIENT_ID"
    )
    end = next(
        index for index, node in enumerate(tree.body)
        if isinstance(node, ast.FunctionDef) and node.name == "cleanup"
    )
    namespace = {
        "mqtt": mqtt_module,
        "MQTT_CLIENT_ID": client_id,
        "MQTT_CLEAN_SESSION": clean_session,
        "APPNAME": "mqtt-gpio-monitor",
        "os": SimpleNamespace(getpid=lambda: 123),
        "logging": Mock(),
        "time": Mock(),
        "MQTT_HOST": "localhost",
        "MQTT_PORT": 1883,
        "MQTT_TOPIC_IN": "/gpio/in/+",
        "MQTT_QOS": 2,
        "MONITOR_REFRESH": "/gpio/refresh",
        "MQTT_LWT": "/clients/gpio",
    }
    setup = ast.Module(body=tree.body[start:end], type_ignores=[])
    exec(compile(setup, str(SCRIPT), "exec"), namespace)
    return namespace


class MQTTCompatibilityTests(unittest.TestCase):
    def test_installed_paho_client(self):
        namespace = load_mqtt_setup(mqtt)
        client = namespace["mqttc"]
        self.addCleanup(client.disconnect)
        self.assertEqual(client._client_id, b"gpio-monitor")
        self.assertFalse(client._clean_session)
        if hasattr(mqtt, "CallbackAPIVersion"):
            self.assertEqual(client.callback_api_version, mqtt.CallbackAPIVersion.VERSION1)

    def test_paho_1_constructor(self):
        def legacy_client(client_id="", clean_session=True):
            return SimpleNamespace(client_id=client_id, clean_session=clean_session)

        namespace = load_mqtt_setup(SimpleNamespace(Client=legacy_client))
        self.assertEqual(namespace["mqttc"].client_id, "gpio-monitor")
        self.assertFalse(namespace["mqttc"].clean_session)

    def test_generated_client_id_requires_clean_session(self):
        namespace = load_mqtt_setup(SimpleNamespace(Client=Mock()), client_id="")
        self.assertEqual(namespace["MQTT_CLIENT_ID"], "mqtt-gpio-monitor_123")
        self.assertTrue(namespace["MQTT_CLEAN_SESSION"])
        namespace["mqtt"].Client.assert_called_once_with(
            client_id="mqtt-gpio-monitor_123", clean_session=True
        )

    def test_connection_callbacks(self):
        client = Mock()
        namespace = load_mqtt_setup(SimpleNamespace(Client=Mock(return_value=client)))
        namespace["on_connect"](client, None, {}, 0)
        self.assertEqual(client.subscribe.call_count, 2)
        client.subscribe.assert_any_call("/gpio/in/+", qos=2)
        client.subscribe.assert_any_call("/gpio/refresh", qos=0)
        client.publish.assert_called_once_with("/clients/gpio", "1", qos=0, retain=True)
        namespace["on_disconnect"](client, None, 0)
        namespace["time"].sleep.assert_not_called()
        namespace["on_disconnect"](client, None, 1)
        namespace["time"].sleep.assert_called_once_with(5)

    def test_refused_connection_does_not_subscribe_or_publish(self):
        for result_code in range(1, 7):
            with self.subTest(result_code=result_code):
                client = Mock()
                namespace = load_mqtt_setup(
                    SimpleNamespace(Client=Mock(return_value=client))
                )
                namespace["on_connect"](client, None, {}, result_code)
                client.subscribe.assert_not_called()
                client.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
