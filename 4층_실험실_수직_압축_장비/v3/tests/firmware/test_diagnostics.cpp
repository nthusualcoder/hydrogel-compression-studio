#include "Arduino.h"
#include <cassert>
#include <fstream>
#include <iostream>
#include <sstream>
TestSerial Serial;
uint64_t testClock = 0;
uint8_t pinLevels[16] = {};
std::vector<uint32_t> risingEdges;
unsigned long micros() { return static_cast<uint32_t>(testClock); }
unsigned long millis() { return static_cast<uint32_t>(testClock / 1000); }
void delayMicroseconds(unsigned int value) { testClock += value; }
void digitalWrite(uint8_t pin, uint8_t value) {
    if (pin == 3 && value && !pinLevels[pin]) risingEdges.push_back(micros());
    pinLevels[pin] = value;
}
void pinMode(uint8_t, uint8_t) {}
void yield() {}
void processCommand(const String&);
void haltNow();
#include "../../firmware/compression_4F_uno_r4/compression_4F_uno_r4.ino"

void resetRig() {
    diagEnabled = false;
    diagHead = diagCount = 0;
    diagTxLength = 0;
    diagSequence = diagDropped = diagSkipped = diagLastStatus = 0;
    testClock = 0;
    std::fill(std::begin(pinLevels), std::end(pinLevels), 0);
    stepper = TraceStepper();
    currentMode = STOPPED;
    inputBuffer = "";
    Serial = TestSerial();
    setup();
    Serial.output.clear();
    risingEdges.clear();
}

void runFor(uint64_t duration) {
    uint64_t until = testClock + duration;
    while (testClock < until) { loop(); testClock += 100; }
}

std::vector<std::vector<std::string>> stepRows(const std::string& text) {
    std::vector<std::vector<std::string>> rows;
    std::istringstream stream(text);
    std::string line;
    while (std::getline(stream, line)) {
        if (line.rfind("DBG,S,", 0) != 0) continue;
        std::istringstream fields(line);
        std::vector<std::string> row;
        std::string field;
        while (std::getline(fields, field, ',')) row.push_back(field);
        assert(row.size() == 6);
        rows.push_back(row);
    }
    return rows;
}

int main(int argc, char** argv) {
    std::string sample;
    for (const char* speed : {"0.00125", "0.0025", "0.00375", "0.005"}) {
        std::string command = std::string("start,0.0125,") + speed;
        resetRig();
        processCommand(command.c_str());
        runFor(12000000);
        const auto baseline = risingEdges;
        assert(baseline.size() == 10);
        assert(Serial.output.find("DBG,") == std::string::npos);
        resetRig();
        processCommand("DIAG,ON");
        processCommand(command.c_str());
        runFor(12000000);
        processCommand("DIAG,OFF");
        runFor(10000);
        assert(risingEdges == baseline);  // 진단 ON/OFF에서 구동 펄스가 동일하다.
        const auto rows = stepRows(Serial.output);
        assert(rows.size() == baseline.size());
        for (size_t i = 0; i < rows.size(); ++i) {
            assert(std::stoul(rows[i][2]) == risingEdges[i]);
            assert(std::stoul(rows[i][3]) == i + 1);
            assert(std::stol(rows[i][4]) == static_cast<long>(i + 1));
        }
        assert(Serial.output.find("DONE\n") != std::string::npos);
        assert(Serial.output.find("DBG,OFF,") != std::string::npos);
        if (std::string(speed) == "0.0025") sample = Serial.output;
    }
    resetRig();
    processCommand("DIAG,ON");
    Serial.writable = 0;
    processCommand("start,0.1,0.0025");
    runFor(45000000);
    assert(risingEdges.size() == 80);  // 전송 버퍼가 막혀도 구동을 기다리게 하지 않는다.
    assert(diagDropped > 0);
    Serial.writable = 256;
    runFor(100000);
    processCommand("DIAG,OFF");
    runFor(100000);
    assert(Serial.output.find("DBG,OFF,") != std::string::npos);
    resetRig();
    processCommand("DIAG,ON");
    processCommand("start,0.1,0.5");
    runFor(2000000);
    processCommand("DIAG,OFF");
    runFor(10000);
    assert(risingEdges.size() == 80);
    assert(stepRows(Serial.output).empty());
    assert(diagSkipped == 80);
    if (argc > 1) std::ofstream(argv[1]) << sample;
    std::cout << "Firmware host checks passed: GPIO edge timestamps, four low speeds, unchanged motion, buffer overflow, high-speed omissions.\n";
}
