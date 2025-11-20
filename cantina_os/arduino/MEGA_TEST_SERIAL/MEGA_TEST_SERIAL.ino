/*
 * MEGA 2560 Serial Communication Test & Explanation
 *
 * This sketch helps you understand:
 * 1. What the serial buffer is
 * 2. How serial communication works
 * 3. The difference between Serial pins and PWM pins
 * 4. How to test buffer overflow
 */

// CRITICAL: Define buffer size BEFORE Arduino.h include!
#define SERIAL_RX_BUFFER_SIZE 256     // Upgraded for MEGA (was 64)
#define SERIAL_TX_BUFFER_SIZE 256     // Also increase TX buffer

#include <Arduino.h>

void setup() {
  // UNDERSTANDING SERIAL vs PWM PINS:
  // ==================================

  // SERIAL PINS (for communication with computer/Python):
  // - Serial0 (USB): TX0=Pin1, RX0=Pin0 (connected to USB port)
  // - Serial1: TX1=Pin18, RX1=Pin19
  // - Serial2: TX2=Pin16, RX2=Pin17
  // - Serial3: TX3=Pin14, RX3=Pin15

  // PWM PINS (for LEDs/Motors - NOT serial!):
  // - Pin 5, 6 are PWM outputs for controlling LED brightness
  // - These send POWER, not DATA
  // - Your WS2812B LEDs use these for the data signal

  // Initialize Serial0 (USB connection to computer)
  Serial.begin(115200);
  while (!Serial) {
    ; // Wait for serial port to connect (needed for native USB)
  }

  Serial.println("===========================================");
  Serial.println("MEGA 2560 Serial Buffer Test");
  Serial.println("===========================================");
  Serial.println("");

  // Show current configuration
  Serial.print("Board: Arduino MEGA 2560 (ATmega2560)\n");
  Serial.print("SRAM: 8192 bytes total\n");
  Serial.print("Serial Ports: 4 hardware UARTs\n");
  Serial.print("Baud Rate: 115200 (bits per second)\n");
  Serial.print("At 115200 baud: ~11,520 bytes/second max\n");
  Serial.print("That's ~11.5 bytes per millisecond!\n");
  Serial.println("");

  // Display buffer info
  #ifdef SERIAL_RX_BUFFER_SIZE
    Serial.print("RX Buffer Size: ");
    Serial.print(SERIAL_RX_BUFFER_SIZE);
    Serial.println(" bytes");
  #else
    Serial.println("RX Buffer Size: 64 bytes (default)");
  #endif

  Serial.println("");
  Serial.println("WHAT IS THE BUFFER?");
  Serial.println("==================");
  Serial.println("The buffer is temporary storage for incoming serial data.");
  Serial.println("When Python sends data:");
  Serial.println("1. Data arrives at RX pin");
  Serial.println("2. Arduino stores it in buffer");
  Serial.println("3. Your code reads from buffer with Serial.read()");
  Serial.println("4. If buffer fills before you read it = OVERFLOW!");
  Serial.println("");

  Serial.println("PIN EXPLANATION:");
  Serial.println("================");
  Serial.println("TX/RX Pins (Serial Communication):");
  Serial.println("- Used to talk to computer/Python");
  Serial.println("- Send/receive text commands");
  Serial.println("- Currently using Serial0 (USB)");
  Serial.println("");
  Serial.println("PWM Pins 5 & 6 (Your LEDs):");
  Serial.println("- NOT for serial communication!");
  Serial.println("- Send power/data to WS2812B LED strips");
  Serial.println("- FastLED library controls these");
  Serial.println("- Nothing to do with serial buffer");
  Serial.println("");

  Serial.println("TESTING MODES:");
  Serial.println("==============");
  Serial.println("1. Send 'TEST' to test buffer capacity");
  Serial.println("2. Send 'FLOOD' to simulate buffer overflow");
  Serial.println("3. Send 'CHECK' to see current buffer status");
  Serial.println("4. Send 'AUTO' for automated buffer test");
  Serial.println("5. Send any text to echo it back");
  Serial.println("");
  Serial.println("Ready for commands...");
  Serial.println("");
}

int totalBytesReceived = 0;
unsigned long lastReportTime = 0;

void loop() {
  // Check for incoming serial data
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim(); // Remove whitespace

    if (command == "TEST") {
      testBufferCapacity();
    }
    else if (command == "FLOOD") {
      simulateOverflow();
    }
    else if (command == "CHECK") {
      checkBufferStatus();
    }
    else if (command == "AUTO") {
      automatedBufferTest();
    }
    else {
      // Echo back what was received
      Serial.print("Received: '");
      Serial.print(command);
      Serial.print("' (");
      Serial.print(command.length());
      Serial.println(" bytes)");

      // Add to total
      totalBytesReceived += command.length();
    }
  }

  // Report stats every 5 seconds
  if (millis() - lastReportTime > 5000) {
    Serial.print("Stats: ");
    Serial.print(totalBytesReceived);
    Serial.print(" total bytes received, buffer has ");
    Serial.print(Serial.available());
    Serial.println(" bytes waiting");
    lastReportTime = millis();
    totalBytesReceived = 0;
  }
}

void testBufferCapacity() {
  Serial.println("\n--- BUFFER CAPACITY TEST ---");
  Serial.println("I will now wait 2 seconds without reading serial...");
  Serial.println("Send data NOW to test buffer capacity!");
  Serial.flush(); // Make sure message is sent

  delay(2000); // Wait 2 seconds (simulating busy Arduino)

  int bytesInBuffer = Serial.available();
  Serial.print("After 2 second delay, buffer has: ");
  Serial.print(bytesInBuffer);
  Serial.println(" bytes");

  if (bytesInBuffer >= 64) {
    Serial.println("⚠️ Buffer is at or over 64 bytes!");
    if (bytesInBuffer >= 256) {
      Serial.println("⚠️ Even 256 byte buffer is full!");
    }
  }

  // Clear the buffer
  while (Serial.available() > 0) {
    Serial.read();
  }
  Serial.println("Buffer cleared.\n");
}

void simulateOverflow() {
  Serial.println("\n--- SIMULATING YOUR PROBLEM ---");
  Serial.println("This simulates Python sending commands while Arduino is busy...");
  Serial.println("NOW SEND DATA FROM PYTHON! You have 3 seconds...");
  Serial.flush();

  delay(1000); // Give user time to start sending

  Serial.println("Arduino now busy for 500ms (NOT reading serial)...");
  unsigned long startTime = millis();

  // Simulate being busy processing LED animations
  // During this time, Python should be flooding us with data
  while (millis() - startTime < 500) {
    // Busy doing LED updates
    // NOT reading serial!
    // Python should be sending data NOW
  }

  // Now check what happened
  int missed = Serial.available();
  Serial.print("While busy, ");
  Serial.print(missed);
  Serial.println(" bytes accumulated in buffer!");

  if (missed >= 64) {
    Serial.println("❌ DEFAULT 64-byte buffer would OVERFLOW!");
    Serial.println("   Commands would be LOST!");
  }
  if (missed >= 256) {
    Serial.println("❌ Even 256-byte buffer overflowed!");
  }
  else if (missed < 256) {
    Serial.println("✅ 256-byte buffer saved us - no data lost!");
  }

  // Clear buffer
  while (Serial.available() > 0) {
    Serial.read();
  }
  Serial.println("Buffer cleared.\n");
}

void checkBufferStatus() {
  Serial.println("\n--- BUFFER STATUS ---");
  Serial.print("Bytes currently in buffer: ");
  Serial.println(Serial.available());
  Serial.print("Buffer size: ");
  #ifdef SERIAL_RX_BUFFER_SIZE
    Serial.print(SERIAL_RX_BUFFER_SIZE);
  #else
    Serial.print("64 (default)");
  #endif
  Serial.println(" bytes");
  Serial.print("Free space: ");
  #ifdef SERIAL_RX_BUFFER_SIZE
    Serial.print(SERIAL_RX_BUFFER_SIZE - Serial.available());
  #else
    Serial.print(64 - Serial.available());
  #endif
  Serial.println(" bytes");
  Serial.println("");
}

void automatedBufferTest() {
  Serial.println("\n--- AUTOMATED BUFFER TEST ---");
  Serial.println("This will signal Python when to send data.");
  Serial.println("");

  // Signal Python to start sending
  Serial.println("SEND_NOW");
  Serial.flush();

  // Now Arduino is busy for 500ms
  unsigned long startTime = millis();
  while (millis() - startTime < 500) {
    // Simulating LED updates
    // NOT reading serial!
  }

  // Check buffer
  int bytesReceived = Serial.available();
  Serial.print("Received ");
  Serial.print(bytesReceived);
  Serial.println(" bytes while busy");

  if (bytesReceived == 0) {
    Serial.println("❌ NO DATA RECEIVED - Python script not sending?");
  }
  else if (bytesReceived >= 64 && bytesReceived < 256) {
    Serial.println("⚠️ Default 64-byte buffer would have OVERFLOWED!");
    Serial.println("✅ But 256-byte buffer handled it!");
  }
  else if (bytesReceived >= 256) {
    Serial.println("❌ Even 256-byte buffer OVERFLOWED!");
  }
  else {
    Serial.print("✅ Buffer handled ");
    Serial.print(bytesReceived);
    Serial.println(" bytes without overflow");
  }

  // Clear buffer
  int cleared = 0;
  while (Serial.available() > 0) {
    Serial.read();
    cleared++;
  }
  Serial.print("Cleared ");
  Serial.print(cleared);
  Serial.println(" bytes from buffer\n");
}