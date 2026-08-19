# Host-side tests for the firmware's pure logic. No ESP32, no toolchain, no hardware.
CXX ?= g++
CXXFLAGS ?= -std=c++17 -Wall -Wextra -Werror -O2

BUILD_DIR := build
BINARY := $(BUILD_DIR)/test_firmware_logic

.PHONY: test clean
test: $(BINARY)
	./$(BINARY)

$(BINARY): test_firmware_logic.cpp $(wildcard ../../firmware/include/*.h)
	@mkdir -p $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) -o $@ $<

clean:
	rm -rf $(BUILD_DIR)
