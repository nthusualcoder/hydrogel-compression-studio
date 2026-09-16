#include <AccelStepper.h>

// 모터 핀 설정 (정확히 맞춰두었습니다)
#define DIR_PIN 2
#define STEP_PIN 3

// 1mm 당 스텝 수 (장비에 맞게 조절하세요. 예: 800)
const float STEPS_PER_MM = 800.0; 

// AccelStepper::DRIVER 모드는 (드라이버모드, STEP핀, DIR핀) 순서로 입력합니다.
AccelStepper stepper(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

enum Mode { STOPPED, AUTO_MOVE, JOG_MOVE };
Mode currentMode = STOPPED;

String inputBuffer = "";

void setup() {
  Serial.begin(115200); 
  
  // ⭐ 모터가 안 도는 증상을 해결하는 핵심 코드!
  // 아두이노의 펄스 신호를 길게 만들어 드라이버가 확실히 인식하게 합니다.
  stepper.setMinPulseWidth(20);

  stepper.setMaxSpeed(4000.0);
  stepper.setAcceleration(2000.0);
  Serial.println("ARDUINO_READY");
}

void loop() {
  // 1. 파이썬 명령 수신
  if (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      inputBuffer.trim();
      processCommand(inputBuffer);
      inputBuffer = "";
    } else {
      inputBuffer += c;
    }
  }

  // 2. 모터 상시 구동 로직
  if (currentMode != STOPPED) {
    stepper.run();
    
    // 자동 압축 모드일 때만 목표 지점에서 스스로 정지
    if (currentMode == AUTO_MOVE && stepper.distanceToGo() == 0) {
      currentMode = STOPPED;
      Serial.println("DONE");
    }
  }
}

void processCommand(String cmd) {
  if (cmd == "STOP") {
    stepper.stop();
    currentMode = STOPPED;
    Serial.println("STOPPED");
  } 
  else if (cmd.startsWith("start,")) {
    int comma1 = cmd.indexOf(',');
    int comma2 = cmd.indexOf(',', comma1 + 1);
    if (comma1 > 0 && comma2 > 0) {
      float dist = cmd.substring(comma1 + 1, comma2).toFloat();
      float speed = cmd.substring(comma2 + 1).toFloat();
      
      stepper.setMaxSpeed(speed * STEPS_PER_MM);
      stepper.move(dist * STEPS_PER_MM); 
      currentMode = AUTO_MOVE;
    }
  }
  else if (cmd.startsWith("JU,")) { 
    // 수동 전진 (파이썬에서 누르고 있을 때)
    float speed = cmd.substring(3).toFloat();
    stepper.setMaxSpeed(speed * STEPS_PER_MM);
    stepper.move(1000000); // 멈출 때까지 계속 이동
    currentMode = JOG_MOVE;
  }
  else if (cmd.startsWith("JD,")) { 
    // 수동 후진
    float speed = cmd.substring(3).toFloat();
    stepper.setMaxSpeed(speed * STEPS_PER_MM);
    stepper.move(-1000000); 
    currentMode = JOG_MOVE;
  }
}