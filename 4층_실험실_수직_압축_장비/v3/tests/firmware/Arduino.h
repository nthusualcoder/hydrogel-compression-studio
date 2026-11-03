#pragma once
// 호스트 테스트용 Arduino API. 실제 AccelStepper와 .ino 소스를 함께 실행한다.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <vector>
using boolean = bool;
constexpr int HIGH = 1, LOW = 0, OUTPUT = 1;
template<class T> T max(T a, T b) { return a > b ? a : b; }
template<class T> T constrain(T value, T lo, T hi) { return std::max(lo, std::min(value, hi)); }
unsigned long micros();
unsigned long millis();
void delayMicroseconds(unsigned int value);
void digitalWrite(uint8_t pin, uint8_t value);
void pinMode(uint8_t pin, uint8_t mode);
void yield();

class String {
public:
    String(const char* text = "") : value(text) {}
    String(std::string text) : value(std::move(text)) {}
    bool operator==(const char* other) const { return value == other; }
    String& operator+=(char c) { value += c; return *this; }
    const char* c_str() const { return value.c_str(); }
    bool startsWith(const char* prefix) const { return value.rfind(prefix, 0) == 0; }
    int indexOf(char c, int start = 0) const {
        auto at = value.find(c, start); return at == std::string::npos ? -1 : static_cast<int>(at);
    }
    String substring(int start, int end = -1) const {
        return value.substr(start, end < 0 ? std::string::npos : end - start);
    }
    float toFloat() const { return static_cast<float>(std::atof(value.c_str())); }
    void trim() {
        auto begin = value.find_first_not_of(" \r\n\t");
        value = begin == std::string::npos ? "" : value.substr(begin, value.find_last_not_of(" \r\n\t") - begin + 1);
    }
private:
    std::string value;
};

struct TestSerial {
    std::string input, output;
    int writable = 256;
    void begin(int) {}
    int available() { return static_cast<int>(input.size()); }
    int read() { char c = input.front(); input.erase(0, 1); return static_cast<unsigned char>(c); }
    int availableForWrite() { return writable; }
    size_t write(const uint8_t* data, size_t size) { output.append(reinterpret_cast<const char*>(data), size); return size; }
    void println(const char* text) { output += text; output += '\n'; }
};
extern TestSerial Serial;
