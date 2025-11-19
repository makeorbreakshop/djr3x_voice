# Automated End-to-End System Testing Research

Research on how real-world projects do automated E2E testing with REAL applications (not mocks).

---

## Table of Contents
1. [CLI Applications](#1-cli-applications)
2. [ROS/Robotics Systems](#2-rosrobotics-systems)
3. [Daemons/Services](#3-daemonsservices)
4. [Voice Assistants / Interactive Systems](#4-voice-assistants--interactive-systems)
5. [Event-Driven Systems](#5-event-driven-systems)
6. [Universal Best Practices](#6-universal-best-practices)

---

## 1. CLI Applications

### Overview
CLI tools test themselves end-to-end by spawning the actual binary, injecting stdin, and capturing stdout/stderr.

### Key Tools & Libraries

#### **pexpect** - Industry Standard for Interactive Programs
- **GitHub**: https://github.com/pexpect/pexpect
- **Purpose**: Control interactive programs in pseudo-terminal
- **Used by**: ssh, ftp, passwd, telnet automation, CLI testing

**Basic Usage Pattern**:
```python
import pexpect

def test_interactive_cli():
    # Spawn the actual CLI application
    child = pexpect.spawn('python my_cli.py')

    # Wait for prompt and send input
    child.expect('Enter your name:')
    child.sendline('Alice')

    # Verify output
    child.expect('Hello, Alice!')

    # Check exit code
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0
```

#### **pytest-docker-pexpect** - Testing in Containers
- **GitHub**: https://github.com/nvbn/pytest-docker-pexpect
- **Purpose**: Combine pexpect with Docker for isolated testing
- **PyPI**: `pip install pytest-docker-pexpect`

**Example from Real Projects**:
```python
import pytest
from pexpect import TIMEOUT

def test_echo(spawnu):
    # spawnu fixture runs command inside Docker container
    proc = spawnu(u'ubuntu', u'FROM ubuntu:latest', u'bash')
    proc.sendline(u'ls')
    assert proc.expect([TIMEOUT, u'bin'])  # Expect 'bin' directory

    # Access Docker-specific info
    container_id = proc.docker_container_id
    stats = proc.docker_stats()  # Returns JSON
    inspect = proc.docker_inspect()  # Returns JSON

def test_with_docker_args(spawnu):
    proc = spawnu(
        u'ubuntu',
        u'FROM ubuntu:latest',
        u'bash',
        docker_run_arguments=[u'--expose', u'80']
    )
    proc.sendline(u'echo $HOME')
    proc.expect(u'/root')
```

#### **pytest with subprocess** - Direct Process Testing
- **Docs**: https://docs.pytest.org/
- **Pattern**: Use `subprocess.run()` with `input` parameter

**Example Pattern**:
```python
import subprocess
import pytest

def test_cli_with_stdin():
    # Run actual CLI with stdin input
    result = subprocess.run(
        ['python', 'my_cli.py'],
        input='Alice\n25\n',  # Simulate user input
        capture_output=True,
        text=True,
        timeout=5
    )

    # Assertions on real output
    assert result.returncode == 0
    assert 'Hello, Alice' in result.stdout
    assert result.stderr == ''

def test_cli_with_capfd(capfd):
    # capfd captures subprocess stdout/stderr at FD level
    result = subprocess.run(['ls', '-la'], check=True)
    captured = capfd.readouterr()
    assert 'total' in captured.out
```

### Real Project Examples

#### **Docker CLI Testing**
- **GitHub**: https://github.com/docker/cli/blob/master/TESTING.md
- **Tool**: gotestyourself/icmd (Go equivalent of subprocess testing)

**Docker's E2E Testing Strategy** (from TESTING.md):
- Tests located in `./e2e` directory
- Each subdirectory mirrors `cli/command` structure
- Tests run the actual `docker` binary
- Assertions on exit codes, stdout, stderr, filesystem state
- One success-case E2E test per feature/subcommand
- Limited critical error path coverage

**Test Pattern** (conceptual Python equivalent):
```python
def test_docker_run():
    # Execute real docker binary
    result = icmd.run('docker', 'run', 'ubuntu', 'echo', 'hello')

    # Assert on actual behavior
    assert result.exit_code == 0
    assert 'hello' in result.stdout
    assert result.stderr == ''

    # Verify filesystem state changed
    containers = icmd.run('docker', 'ps', '-a')
    assert 'ubuntu' in containers.stdout
```

#### **pytest-subprocess Plugin**
- **GitHub**: https://github.com/aklajnert/pytest-subprocess
- **Purpose**: Mock subprocess calls (for unit tests) but also supports real subprocess testing
- **PyPI**: `pip install pytest-subprocess`

**stdin_callable Example**:
```python
def test_with_stdin_processing(fp):
    # Register command with stdin processing
    def process_stdin(stdin_data):
        if stdin_data == b'password\n':
            return {'stdout': b'Access granted'}
        return {'stdout': b'Access denied'}

    fp.register(
        ['./auth_cli'],
        stdin_callable=process_stdin
    )

    # Test will process stdin dynamically
    result = subprocess.run(
        ['./auth_cli'],
        input=b'password\n',
        capture_output=True
    )
    assert b'Access granted' in result.stdout
```

### Cleanup Best Practices for CLI Testing

**Use Pytest Fixtures with Finalizers**:
```python
import pytest
import subprocess
import signal

@pytest.fixture(scope='session')
def cli_server(request):
    # Start a long-running CLI process
    proc = subprocess.Popen(
        ['python', 'server.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Wait for startup
    time.sleep(2)
    assert proc.poll() is None, "Server failed to start"

    # Register cleanup that ALWAYS runs
    def cleanup():
        proc.terminate()  # SIGTERM (graceful)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()  # SIGKILL (forceful)

    request.addfinalizer(cleanup)

    yield proc

def test_server_responds(cli_server):
    # Test against real running server
    result = subprocess.run(
        ['curl', 'http://localhost:8000/health'],
        capture_output=True
    )
    assert result.returncode == 0
```

**Alternative: Yield Pattern**:
```python
@pytest.fixture(scope='session')
def cli_server():
    proc = subprocess.Popen(['python', 'server.py'])
    time.sleep(2)
    yield proc

    # Cleanup after yield
    proc.terminate()
    proc.wait(timeout=5)
```

---

## 2. ROS/Robotics Systems

### Overview
ROS (Robot Operating System) uses `rostest` for integration testing with real nodes running.

### Key Tools & Libraries

#### **rostest** (ROS 1)
- **Docs**: http://wiki.ros.org/rostest
- **Purpose**: Integration test suite based on roslaunch, compatible with xUnit
- **Pattern**: Launch real nodes and test their behavior via topic/service interactions

#### **launch_testing** (ROS 2)
- **Docs**: https://index.ros.org/p/launch_testing/
- **Purpose**: Framework for launch integration testing
- **Features**:
  - Exit codes of all processes available to tests
  - Tests can check that all processes shut down normally
  - Tests can fail when a process dies unexpectedly

### Real Project Examples

#### **MIT RSS rostest_example**
- **GitHub**: https://github.com/mit-rss/rostest_example
- **Purpose**: Tests wall_detector node that processes ScanPoints and publishes Line messages

**Test File Structure** (`wall_detector_test.py`):
```python
#!/usr/bin/env python
import unittest
import rospy
from rostest_example.msg import ScanPoints, Line

class WallDetectorTest(unittest.TestCase):
    def setUp(self):
        # Subscribe to output topic from REAL node
        rospy.Subscriber('/detected_line', Line, self.line_callback)
        self.pub = rospy.Publisher('/scan_points', ScanPoints, queue_size=10)
        self.received_line = None
        rospy.sleep(1)  # Wait for connections

    def line_callback(self, msg):
        self.received_line = msg

    def test_vertical_line(self):
        # Publish test data to REAL node
        test_points = ScanPoints()
        test_points.x = [1.0, 1.0, 1.0, 1.0]
        test_points.y = [0.0, 1.0, 2.0, 3.0]

        self.pub.publish(test_points)

        # Wait for REAL node to process and publish result
        timeout = rospy.Time.now() + rospy.Duration(5)
        while not self.received_line and rospy.Time.now() < timeout:
            rospy.sleep(0.1)

        # Assert on actual node behavior
        self.assertIsNotNone(self.received_line)
        self.assertAlmostEqual(self.received_line.slope, float('inf'))
        self.assertEqual(self.received_line.intercept, 1.0)

if __name__ == '__main__':
    import rostest
    rospy.init_node('wall_detector_test')
    rostest.rosrun('rostest_example', 'wall_detector_test', WallDetectorTest)
```

**Launch File** (`test-wall-detector.launch`):
```xml
<launch>
  <!-- Start the REAL node being tested -->
  <node name="wall_detector" pkg="rostest_example" type="wall_detector.py" />

  <!-- Start the test node (marked with test="test-wall-detector") -->
  <test test-name="wall_detector_test" pkg="rostest_example" type="wall_detector_test.py" />
</launch>
```

**Running the Test**:
```bash
rostest rostest_example test-wall-detector.launch
```

#### **steup/Ros-Test-Example**
- **GitHub**: https://github.com/steup/Ros-Test-Example
- **Purpose**: Car simulation with GTest and Rostest examples

**Built-in Tests Available**:
- `hztest`: Tests publish frequency of nodes
- `paramtest`: Tests parameter server values
- `publishtest`: Tests if topics are being published

**Example hztest Usage**:
```xml
<launch>
  <node name="camera_node" pkg="usb_cam" type="usb_cam_node" />

  <!-- Test that camera publishes at 30Hz -->
  <test test-name="camera_frequency" pkg="rostest" type="hztest">
    <param name="topic" value="/camera/image_raw" />
    <param name="hz" value="30.0" />
    <param name="hzerror" value="5.0" />  <!-- ±5 Hz tolerance -->
    <param name="test_duration" value="5.0" />
  </test>
</launch>
```

### ROS Testing Levels

**From ROS Industrial Training**:
1. **Level 1**: Unit tests (no ROS, just Python/C++ logic)
2. **Level 2**: ROS node unit test (rostest + unittest/gtest)
3. **Level 3**: ROS nodes integration test (multiple nodes, rostest)
4. **Level 4**: Functional testing (full robot application, real or simulated hardware)

### Cleanup Practices for ROS Tests

**Automatic Cleanup**:
- rostest automatically kills all nodes when test completes
- Use `<test>` tag in launch files (not `<node>` tag) for test nodes
- Test nodes have 10 second timeout by default

**Manual Cleanup Between Tests**:
```python
class MyNodeTest(unittest.TestCase):
    def setUp(self):
        # Clear test queue before each test
        rospy.wait_for_service('/test_service/reset')
        reset = rospy.ServiceProxy('/test_service/reset', Empty)
        reset()

    def tearDown(self):
        # Ensure clean state
        rospy.sleep(0.5)
```

---

## 3. Daemons/Services

### Overview
Daemons and services are tested by starting real instances (often in Docker) and verifying behavior.

### Key Tools & Libraries

#### **Testcontainers** - Industry Standard for Real Dependencies
- **GitHub**: https://github.com/testcontainers/testcontainers-python
- **Purpose**: Disposable Docker containers for testing
- **PyPI**: `pip install testcontainers`
- **Supports**: PostgreSQL, MySQL, Redis, MongoDB, Kafka, etc.

**Basic Pattern**:
```python
from testcontainers.postgres import PostgresContainer
import sqlalchemy

def test_database_operations():
    with PostgresContainer("postgres:16") as postgres:
        # Get connection to REAL PostgreSQL instance
        engine = sqlalchemy.create_engine(postgres.get_connection_url())

        with engine.begin() as connection:
            # Test against actual database
            connection.execute(sqlalchemy.text(
                "CREATE TABLE users (id SERIAL, name VARCHAR(100))"
            ))
            connection.execute(sqlalchemy.text(
                "INSERT INTO users (name) VALUES ('Alice')"
            ))
            result = connection.execute(sqlalchemy.text(
                "SELECT name FROM users"
            ))
            name = result.fetchone()[0]
            assert name == 'Alice'

    # Container automatically destroyed after 'with' block
```

**With Pytest Fixtures**:
```python
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

@pytest.fixture(scope="module")
def postgres():
    with PostgresContainer("postgres:16") as container:
        yield container

@pytest.fixture(scope="module")
def redis():
    with RedisContainer("redis:7") as container:
        yield container

def test_with_real_postgres(postgres):
    # Test with actual PostgreSQL
    conn_url = postgres.get_connection_url()
    # ... test code ...

def test_with_real_redis(redis):
    # Test with actual Redis
    import redis as redis_client
    client = redis_client.Redis.from_url(redis.get_connection_url())
    client.set('key', 'value')
    assert client.get('key') == b'value'
```

#### **pytest-docker** - Docker Compose Integration
- **GitHub**: https://github.com/avast/pytest-docker
- **Purpose**: Start Docker Compose services for pytest
- **PyPI**: `pip install pytest-docker`

**Example with docker-compose.yml**:
```yaml
# docker-compose.yml
version: '3'
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: test
      POSTGRES_DB: testdb

  redis:
    image: redis:7

  nginx:
    image: nginx:latest
    ports:
      - "8080:80"
```

**Test File**:
```python
import pytest
import requests

@pytest.fixture(scope="session")
def docker_compose_file(pytestconfig):
    return pytestconfig.rootdir / "docker-compose.yml"

def test_nginx_running(docker_services):
    # Wait for nginx to be ready
    docker_services.wait_until_responsive(
        timeout=30.0,
        pause=0.1,
        check=lambda: requests.get("http://localhost:8080").status_code == 200
    )

    # Test against REAL nginx
    response = requests.get("http://localhost:8080")
    assert response.status_code == 200
    assert 'nginx' in response.text.lower()
```

### Real Project Examples

#### **PostgreSQL, Redis, nginx Integration Testing**

**Node.js Example Pattern** (Python equivalent):
```python
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer
import psycopg2
import redis

@pytest.fixture(scope="module")
def postgres_container():
    with PostgresContainer("postgres:16") as container:
        yield container

@pytest.fixture(scope="module")
def redis_container():
    with RedisContainer("redis:7") as container:
        yield container

def test_full_stack_integration(postgres_container, redis_container):
    # Connect to REAL PostgreSQL
    conn = psycopg2.connect(postgres_container.get_connection_url())
    cursor = conn.cursor()

    # Test database operations
    cursor.execute("CREATE TABLE cache_keys (key VARCHAR(100), value TEXT)")
    cursor.execute("INSERT INTO cache_keys VALUES ('user:1', 'cached_data')")
    conn.commit()

    # Connect to REAL Redis
    r = redis.Redis.from_url(redis_container.get_connection_url())

    # Test cache operations
    cursor.execute("SELECT key, value FROM cache_keys")
    for key, value in cursor.fetchall():
        r.set(key, value)

    # Verify integration
    assert r.get('user:1') == b'cached_data'

    cursor.close()
    conn.close()
```

#### **GitHub Actions with Service Containers**

**Example .github/workflows/test.yml**:
```yaml
name: Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: testdb
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

      redis:
        image: redis:7
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v2

      - name: Run integration tests
        env:
          DATABASE_URL: postgresql://postgres:test@localhost/testdb
          REDIS_URL: redis://localhost:6379
        run: |
          python -m pytest tests/integration/
```

### Cleanup Best Practices for Services

**Testcontainers Automatic Cleanup**:
- Containers automatically stopped and removed after `with` block
- Uses Ryuk sidecar container to cleanup even if test crashes
- No manual cleanup needed

**Module-Scoped Fixtures for Performance**:
```python
@pytest.fixture(scope="module")
def postgres_container(request):
    # Start once for entire test module
    container = PostgresContainer("postgres:16")
    container.start()

    # Cleanup via finalizer (more reliable than yield)
    def cleanup():
        container.stop()
    request.addfinalizer(cleanup)

    return container

@pytest.fixture(scope="function", autouse=True)
def clean_database(postgres_container):
    # Clean data between tests (not the container)
    engine = sqlalchemy.create_engine(postgres_container.get_connection_url())
    with engine.begin() as conn:
        # Truncate all tables
        conn.execute(sqlalchemy.text("TRUNCATE TABLE users CASCADE"))
```

**Best Practices**:
1. Use module/session scope for expensive container startup
2. Clean data between tests (function scope), not containers
3. Use finalizers for critical cleanup (more reliable than yield)
4. Testcontainers handles Docker cleanup automatically
5. Dynamic port mapping prevents port collisions in parallel tests

---

## 4. Voice Assistants / Interactive Systems

### Overview
Voice assistants and chatbots test end-to-end by simulating real voice commands and verifying responses.

### Key Tools & Libraries

#### **Bespoken Tools** - Alexa/Google Assistant Testing
- **Website**: https://bespoken.io
- **Purpose**: Automated testing for voice applications without speaking
- **Features**:
  - Tests run against actual Alexa Voice Service (AVS)
  - Tests run against actual Google Assistant
  - No need to speak; uses programmatic API
  - End-to-end coverage including utterance resolution

**Example Test Pattern** (conceptual):
```yaml
# test.yml
---
configuration:
  locale: en-US

---
- test: "Launch skill"
  - open my skill:
      - response.outputSpeech.ssml: "*Welcome to my skill*"
      - response.card.title: "Welcome"

- test: "Utterance handling"
  - what is the weather:
      - response.outputSpeech.ssml: "*sunny*"
  - set a timer for 5 minutes:
      - response.outputSpeech.ssml: "*timer set*"
```

#### **Botium** - Multi-Platform Chatbot Testing
- **Purpose**: Test conversational AI across platforms
- **Supports**: Alexa, Google Home, Facebook Messenger, etc.
- **Features**:
  - Uses Skill Invocation API to invoke applications
  - Uses Skill Simulation API to test skills
  - Verifies utterance-intent mapping

**Botium Example Pattern**:
```javascript
// Conceptual Python equivalent
from botium import BotiumConnectorAlexa

def test_alexa_skill_invocation():
    connector = BotiumConnectorAlexa(
        skill_id='amzn1.ask.skill.xxxx',
        access_token='YOUR_TOKEN'
    )

    # Send utterance to REAL Alexa skill
    response = connector.send_utterance('what is the weather')

    # Verify actual Alexa response
    assert 'sunny' in response.output_speech.lower()
    assert response.intent == 'GetWeatherIntent'
```

#### **Amazon SMAPI** - Skill Management API
- **Docs**: https://developer.amazon.com/docs/smapi/
- **Purpose**: Programmatic skill testing and management
- **Tools**: ASK CLI (Alexa Skills Kit Command Line Interface)

**SMAPI Testing Pattern**:
```python
import subprocess
import json

def test_alexa_skill_with_ask_cli():
    # Use ASK CLI to test actual skill
    result = subprocess.run(
        ['ask', 'api', 'simulate-skill',
         '--skill-id', 'amzn1.ask.skill.xxxx',
         '--text', 'what is the weather',
         '--locale', 'en-US'],
        capture_output=True,
        text=True
    )

    response = json.loads(result.stdout)

    # Verify actual skill behavior
    assert response['result']['skillExecutionInfo']['invocations'][0]['invocationResponse']['body']['response']['outputSpeech']['text'] == 'It is sunny today'
```

### Testing Voice Pipelines (Like DJ R3X)

**Pattern for Testing Voice-Interactive Systems**:
```python
import pytest
import subprocess
import time
import os

@pytest.fixture(scope="session")
def voice_assistant():
    # Start REAL voice assistant application
    proc = subprocess.Popen(
        ['python', '-m', 'cantina_os.main'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # Line buffered
        env={**os.environ, 'TEST_MODE': '1'}
    )

    # Wait for startup
    time.sleep(5)
    assert proc.poll() is None, "Voice assistant failed to start"

    yield proc

    # Cleanup
    proc.stdin.write('quit\n')
    proc.stdin.flush()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

def test_voice_command_list_music(voice_assistant):
    # Send command via stdin (simulating CLI input)
    voice_assistant.stdin.write('list music\n')
    voice_assistant.stdin.flush()

    # Read output from REAL application
    output_lines = []
    for _ in range(10):  # Read next 10 lines
        line = voice_assistant.stdout.readline()
        output_lines.append(line)
        if 'Available tracks:' in line:
            break

    output = ''.join(output_lines)

    # Verify actual behavior
    assert 'Available tracks:' in output
    assert '.mp3' in output or '.wav' in output

def test_voice_command_play_music(voice_assistant):
    voice_assistant.stdin.write('play music 1\n')
    voice_assistant.stdin.flush()

    # Wait for music to start
    time.sleep(2)

    # Verify music is playing (check logs)
    output_lines = []
    for _ in range(20):
        line = voice_assistant.stdout.readline()
        output_lines.append(line)
        if 'MUSIC_PLAYBACK_STARTED' in line:
            break

    output = ''.join(output_lines)
    assert 'MUSIC_PLAYBACK_STARTED' in output
```

### Best Practices for Voice Assistant Testing

1. **Use Environment Variables for Test Mode**:
```python
if os.getenv('TEST_MODE'):
    # Disable microphone input
    # Use mock speech recognition
    # Disable actual TTS playback
```

2. **Test Command Pipeline End-to-End**:
```python
def test_full_voice_pipeline(voice_assistant):
    # 1. Simulate transcription input
    voice_assistant.stdin.write('transcription:play some music\n')

    # 2. Wait for LLM processing
    time.sleep(2)

    # 3. Verify intent extraction
    assert_event_emitted('INTENT_EXECUTION_RESULT',
                         contains={'tool': 'play_music'})

    # 4. Verify music playback started
    assert_event_emitted('MUSIC_PLAYBACK_STARTED')

    # 5. Verify TTS response generated
    assert_event_emitted('SPEECH_SYNTHESIS_STARTED')
```

---

## 5. Event-Driven Systems

### Overview
Kafka, RabbitMQ, and ZeroMQ systems test end-to-end by starting real message brokers and testing producer-consumer flows.

### Key Patterns

#### **Kafka Testing with Testcontainers**
```python
from testcontainers.kafka import KafkaContainer
from kafka import KafkaProducer, KafkaConsumer
import json

@pytest.fixture(scope="module")
def kafka_container():
    with KafkaContainer() as kafka:
        yield kafka

def test_kafka_producer_consumer(kafka_container):
    bootstrap_servers = kafka_container.get_bootstrap_server()

    # Create REAL producer
    producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )

    # Create REAL consumer
    consumer = KafkaConsumer(
        'test-topic',
        bootstrap_servers=bootstrap_servers,
        auto_offset_reset='earliest',
        value_deserializer=lambda m: json.loads(m.decode('utf-8'))
    )

    # Test actual message flow
    test_message = {'user_id': 123, 'action': 'login'}
    producer.send('test-topic', test_message)
    producer.flush()

    # Consume from REAL Kafka
    messages = []
    for msg in consumer:
        messages.append(msg.value)
        break  # Got our message

    assert messages[0] == test_message

    producer.close()
    consumer.close()
```

#### **RabbitMQ Testing**
```python
from testcontainers.rabbitmq import RabbitMqContainer
import pika

@pytest.fixture(scope="module")
def rabbitmq_container():
    with RabbitMqContainer("rabbitmq:3-management") as rabbitmq:
        yield rabbitmq

def test_rabbitmq_queue(rabbitmq_container):
    # Connect to REAL RabbitMQ
    connection = pika.BlockingConnection(
        pika.URLParameters(rabbitmq_container.get_connection_url())
    )
    channel = connection.channel()

    # Declare queue
    channel.queue_declare(queue='test-queue')

    # Publish message
    channel.basic_publish(
        exchange='',
        routing_key='test-queue',
        body=b'Hello World'
    )

    # Consume message
    method_frame, header_frame, body = channel.basic_get('test-queue')
    assert body == b'Hello World'

    channel.basic_ack(method_frame.delivery_tag)
    connection.close()

def test_rabbitmq_multiple_consumers(rabbitmq_container):
    """Test competing consumer pattern with REAL RabbitMQ"""
    connection_url = rabbitmq_container.get_connection_url()

    # Producer
    conn_producer = pika.BlockingConnection(pika.URLParameters(connection_url))
    channel_producer = conn_producer.channel()
    channel_producer.queue_declare(queue='work-queue', durable=True)

    # Send multiple messages
    for i in range(10):
        channel_producer.basic_publish(
            exchange='',
            routing_key='work-queue',
            body=f'Task {i}'.encode(),
            properties=pika.BasicProperties(delivery_mode=2)  # persistent
        )

    # Consumer 1
    conn_consumer1 = pika.BlockingConnection(pika.URLParameters(connection_url))
    channel_consumer1 = conn_consumer1.channel()
    channel_consumer1.basic_qos(prefetch_count=1)

    messages_consumer1 = []
    def callback1(ch, method, properties, body):
        messages_consumer1.append(body.decode())
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel_consumer1.basic_consume(queue='work-queue', on_message_callback=callback1)

    # Consumer 2
    conn_consumer2 = pika.BlockingConnection(pika.URLParameters(connection_url))
    channel_consumer2 = conn_consumer2.channel()
    channel_consumer2.basic_qos(prefetch_count=1)

    messages_consumer2 = []
    def callback2(ch, method, properties, body):
        messages_consumer2.append(body.decode())
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel_consumer2.basic_consume(queue='work-queue', on_message_callback=callback2)

    # Process messages
    for _ in range(5):
        channel_consumer1.connection.process_data_events(time_limit=1)
        channel_consumer2.connection.process_data_events(time_limit=1)

    # Verify load distribution
    assert len(messages_consumer1) + len(messages_consumer2) == 10
    assert len(messages_consumer1) > 0
    assert len(messages_consumer2) > 0

    conn_producer.close()
    conn_consumer1.close()
    conn_consumer2.close()
```

#### **ZeroMQ Testing** (No Broker)
```python
import zmq
import threading
import time

def test_zeromq_pub_sub():
    context = zmq.Context()

    # Publisher
    publisher = context.socket(zmq.PUB)
    publisher.bind("tcp://127.0.0.1:5555")

    # Subscriber
    subscriber = context.socket(zmq.SUB)
    subscriber.connect("tcp://127.0.0.1:5555")
    subscriber.setsockopt_string(zmq.SUBSCRIBE, "")

    # Give time for connection
    time.sleep(0.5)

    # Publish message
    publisher.send_string("Hello ZeroMQ")

    # Receive message
    message = subscriber.recv_string()
    assert message == "Hello ZeroMQ"

    publisher.close()
    subscriber.close()
    context.term()

def test_zeromq_req_rep():
    """Test request-reply pattern"""
    context = zmq.Context()

    # Server (REP socket)
    server = context.socket(zmq.REP)
    server.bind("tcp://127.0.0.1:5556")

    # Client (REQ socket)
    client = context.socket(zmq.REQ)
    client.connect("tcp://127.0.0.1:5556")

    # Server thread
    responses = []
    def server_thread():
        for _ in range(3):
            message = server.recv_string()
            responses.append(message)
            server.send_string(f"ACK: {message}")

    thread = threading.Thread(target=server_thread)
    thread.start()

    # Client sends requests
    for i in range(3):
        client.send_string(f"Request {i}")
        reply = client.recv_string()
        assert reply == f"ACK: Request {i}"

    thread.join()
    assert responses == ["Request 0", "Request 1", "Request 2"]

    client.close()
    server.close()
    context.term()
```

### Event-Driven Testing Best Practices

1. **Clean Queue State Between Tests**:
```python
@pytest.fixture(scope="function", autouse=True)
def clean_queues(rabbitmq_container):
    connection = pika.BlockingConnection(
        pika.URLParameters(rabbitmq_container.get_connection_url())
    )
    channel = connection.channel()

    # Delete test queues
    try:
        channel.queue_delete(queue='test-queue')
    except:
        pass

    connection.close()
```

2. **Test Message Ordering and Idempotency**:
```python
def test_message_ordering(kafka_container):
    producer = KafkaProducer(bootstrap_servers=kafka_container.get_bootstrap_server())

    # Send ordered messages
    for i in range(100):
        producer.send('ordered-topic', key=b'key1', value=f'msg{i}'.encode())
    producer.flush()

    # Verify order maintained
    consumer = KafkaConsumer(
        'ordered-topic',
        bootstrap_servers=kafka_container.get_bootstrap_server(),
        auto_offset_reset='earliest'
    )

    messages = [msg.value.decode() for msg in consumer]
    assert messages == [f'msg{i}' for i in range(100)]
```

3. **Test Failure Scenarios**:
```python
def test_rabbitmq_message_redelivery(rabbitmq_container):
    connection = pika.BlockingConnection(
        pika.URLParameters(rabbitmq_container.get_connection_url())
    )
    channel = connection.channel()
    channel.queue_declare(queue='test-queue')

    # Publish message
    channel.basic_publish(exchange='', routing_key='test-queue', body=b'test')

    # Consumer 1: Get but DON'T ack (simulate failure)
    method1, _, body1 = channel.basic_get('test-queue', auto_ack=False)
    assert body1 == b'test'
    # Intentionally don't ack - simulate consumer crash
    connection.close()

    # Consumer 2: Should receive redelivered message
    connection2 = pika.BlockingConnection(
        pika.URLParameters(rabbitmq_container.get_connection_url())
    )
    channel2 = connection2.channel()
    method2, _, body2 = channel2.basic_get('test-queue', auto_ack=False)

    assert body2 == b'test'
    assert method2.redelivered == True  # Verify redelivery flag

    channel2.basic_ack(method2.delivery_tag)
    connection2.close()
```

---

## 6. Universal Best Practices

### Process Cleanup

**Always Use Fixtures with Finalizers**:
```python
@pytest.fixture(scope='session')
def background_service(request):
    proc = subprocess.Popen(['./service'])

    def cleanup():
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    request.addfinalizer(cleanup)
    return proc
```

**Why Finalizers Over Yield**:
- Finalizers run even if fixture setup fails
- More reliable for critical cleanup
- Can register multiple finalizers

### State Management

**Clean Data, Not Containers**:
```python
@pytest.fixture(scope="module")
def postgres_container():
    # Expensive: Start once per module
    container = PostgresContainer("postgres:16")
    container.start()
    yield container
    container.stop()

@pytest.fixture(scope="function", autouse=True)
def clean_data(postgres_container):
    # Cheap: Clean data before each test
    engine = create_engine(postgres_container.get_connection_url())
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE users CASCADE"))
```

### Timeouts and Waiting

**Always Set Timeouts**:
```python
def test_with_timeout():
    result = subprocess.run(
        ['./slow_command'],
        timeout=30,  # Prevent hanging tests
        capture_output=True
    )
```

**Wait for Readiness**:
```python
def wait_for_service(url, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                return True
        except:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Service at {url} not ready after {timeout}s")

def test_service(docker_services):
    wait_for_service("http://localhost:8080/health")
    # Now run tests
```

### Logging and Debugging

**Capture Output for Debugging**:
```python
def test_with_logging(tmpdir):
    log_file = tmpdir.join("output.log")

    proc = subprocess.Popen(
        ['./app'],
        stdout=open(log_file, 'w'),
        stderr=subprocess.STDOUT
    )

    time.sleep(5)
    proc.terminate()

    # Check logs if test fails
    output = log_file.read()
    assert 'ERROR' not in output
    assert 'Started successfully' in output
```

### Parallel Testing

**Use Dynamic Ports**:
```python
import socket

def get_free_port():
    with socket.socket() as s:
        s.bind(('', 0))
        return s.getsockname()[1]

@pytest.fixture
def test_server():
    port = get_free_port()
    proc = subprocess.Popen(['./server', '--port', str(port)])
    yield f"http://localhost:{port}"
    proc.terminate()
```

### Environment Isolation

**Use Environment Variables**:
```python
def test_with_isolated_env():
    env = os.environ.copy()
    env.update({
        'DATABASE_URL': 'postgresql://test:test@localhost/testdb',
        'REDIS_URL': 'redis://localhost:6379/1',
        'LOG_LEVEL': 'DEBUG',
        'TEST_MODE': '1'
    })

    proc = subprocess.Popen(['./app'], env=env)
```

---

## Summary Table

| Domain | Tools | Real Project Examples | Key Pattern |
|--------|-------|----------------------|-------------|
| **CLI Apps** | pexpect, pytest-subprocess, pytest-docker-pexpect | Docker CLI, kubectl | Spawn real binary, inject stdin, capture stdout/stderr |
| **ROS/Robotics** | rostest, launch_testing | MIT RSS rostest_example | Launch real nodes, test via topics/services |
| **Daemons/Services** | Testcontainers, pytest-docker | PostgreSQL, Redis, nginx | Start real service in Docker, test with real client |
| **Voice Assistants** | Bespoken, Botium, SMAPI | Alexa Skills | Send real utterances to actual service, verify responses |
| **Event-Driven** | Testcontainers (Kafka/RabbitMQ), ZeroMQ | Kafka, RabbitMQ | Start real broker, test producer-consumer flow |

---

## Key Takeaways

1. **Test Real Systems**: Always test against actual binaries/services, not mocks
2. **Use Containers**: Docker/Testcontainers for isolated, reproducible tests
3. **Cleanup is Critical**: Use pytest finalizers for reliable cleanup
4. **Wait for Readiness**: Always check service health before testing
5. **Capture Output**: Log stdout/stderr for debugging failed tests
6. **Set Timeouts**: Prevent hanging tests with subprocess timeouts
7. **Clean Data, Not Containers**: Reuse expensive containers, clean data between tests
8. **Dynamic Ports**: Enable parallel test execution

---

## Tools Summary with Links

- **pexpect**: https://github.com/pexpect/pexpect
- **pytest-docker-pexpect**: https://github.com/nvbn/pytest-docker-pexpect
- **pytest-subprocess**: https://github.com/aklajnert/pytest-subprocess
- **Testcontainers Python**: https://github.com/testcontainers/testcontainers-python
- **pytest-docker**: https://github.com/avast/pytest-docker
- **Docker CLI Tests**: https://github.com/docker/cli/blob/master/TESTING.md
- **rostest examples**: https://github.com/mit-rss/rostest_example
- **ROS Test Example**: https://github.com/steup/Ros-Test-Example

---

*Generated: 2025-11-19*
*Research focus: Real-world automated E2E testing patterns*
