CC ?= cc
AR ?= ar
PREFIX ?= /usr/local
BUILD_DIR ?= build

CPPFLAGS ?=
CFLAGS ?= -O2 -g
WARNINGS := -Wall -Wextra -Wpedantic
STANDARD := -std=c11

INCLUDE_FLAGS := $(CPPFLAGS) -Iinclude
COMPILE_FLAGS := $(CFLAGS) $(STANDARD) $(WARNINGS)
LIB_OBJECTS := $(BUILD_DIR)/mipc.o $(BUILD_DIR)/i18n.o
STATIC_LIBRARY := $(BUILD_DIR)/libmsys-mipc.a
EXAMPLE_BINARY := $(BUILD_DIR)/msys-c-component
TEST_BINARIES := $(BUILD_DIR)/test-mipc $(BUILD_DIR)/test-i18n

.PHONY: all example check strict install clean

all: $(STATIC_LIBRARY) $(EXAMPLE_BINARY)

example: $(EXAMPLE_BINARY)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/mipc.o: src/mipc.c include/msys/mipc.h | $(BUILD_DIR)
	$(CC) $(INCLUDE_FLAGS) $(COMPILE_FLAGS) -c $< -o $@

$(BUILD_DIR)/i18n.o: src/i18n.c include/msys/i18n.h | $(BUILD_DIR)
	$(CC) $(INCLUDE_FLAGS) $(COMPILE_FLAGS) -c $< -o $@

$(STATIC_LIBRARY): $(LIB_OBJECTS)
	$(AR) rcs $@ $^

$(EXAMPLE_BINARY): example/c_component.c $(STATIC_LIBRARY) include/msys/mipc.h | $(BUILD_DIR)
	$(CC) $(INCLUDE_FLAGS) $(COMPILE_FLAGS) $< $(STATIC_LIBRARY) -o $@

$(BUILD_DIR)/test-mipc: tests/test_mipc.c $(STATIC_LIBRARY) include/msys/mipc.h | $(BUILD_DIR)
	$(CC) $(INCLUDE_FLAGS) $(COMPILE_FLAGS) $< $(STATIC_LIBRARY) -o $@

$(BUILD_DIR)/test-i18n: tests/test_i18n.c $(STATIC_LIBRARY) include/msys/i18n.h | $(BUILD_DIR)
	$(CC) $(INCLUDE_FLAGS) $(COMPILE_FLAGS) $< $(STATIC_LIBRARY) -o $@

check: $(TEST_BINARIES)
	$(BUILD_DIR)/test-mipc
	$(BUILD_DIR)/test-i18n

strict:
	$(MAKE) clean
	$(MAKE) CFLAGS="$(CFLAGS) -Werror" all check

install: $(STATIC_LIBRARY)
	install -d $(DESTDIR)$(PREFIX)/include/msys $(DESTDIR)$(PREFIX)/lib
	install -m 0644 include/msys/mipc.h $(DESTDIR)$(PREFIX)/include/msys/mipc.h
	install -m 0644 include/msys/i18n.h $(DESTDIR)$(PREFIX)/include/msys/i18n.h
	install -m 0644 $(STATIC_LIBRARY) $(DESTDIR)$(PREFIX)/lib/libmsys-mipc.a

clean:
	rm -rf $(BUILD_DIR)
