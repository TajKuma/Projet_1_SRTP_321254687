CFLAGS += -std=c99
CFLAGS += -Wall
CFLAGS += -Werror
CFLAGS += -Wshadow
CFLAGS += -Wextra
CFLAGS += -O2 -D_FORTIFY_SOURCE=2
CFLAGS += -fstack-protector-all
CFLAGS += -D_XOPEN_SOURCE -D_POSIX_C_SOURCE=201112L

SOURCES=$(wildcard *.c)
OBJECTS=$(SOURCES:.c=.o)

LDFLAGS= -rdynamic
ifneq ($(shell uname -s),Darwin)
	LDFLAGS += -lrt
endif

.PHONY: all debug clean mrproper clean-tests clean-pycache clean-all rebuild test help

all: link_sim

debug: CFLAGS += -g -DDEBUG -Wno-unused-parameter -fno-omit-frame-pointer
debug: LDFLAGS += -lSegFault
debug: link_sim

link_sim: $(OBJECTS)
	$(CC) $(CFLAGS) $(OBJECTS) -o link_sim $(LDFLAGS)

# Python cache
PYCACHE_DIRS = src/__pycache__ tests/__pycache__ __pycache__
PYCACHE_FILES = src/*.pyc tests/*.pyc *.pyc

# Test-generated files
TEST_FILES = *.bin *.txt *.log *.out link_sim performance_results.png

clean:
	@rm -f $(OBJECTS)
	@echo "  ✓ Object files removed"
	@echo "  ✓ All files cleaned"
	@echo "Removing Python cache..."
	@rm -rf $(PYCACHE_DIRS)
	@rm -f $(PYCACHE_FILES)
	@echo "  ✓ Python cache removed"
	@echo "Removing test files..."
	@rm -f $(TEST_FILES)
	@rm -f tests/*.bin tests/*.txt tests/*.log tests/*.out
	@rm -f src/*.bin src/*.txt src/*.log src/*.out
	@echo "  ✓ Test files removed"
	
mrproper:
	@rm -f link_sim
	@echo "  ✓ Binary removed"



rebuild: clean mrproper link_sim

test: all
	@echo "Running test_perf.py..."
	@python3 tests/test_perf.py || exit 1
	@echo "Running test_srtp.py..."
	@python3 tests/test_srtp.py || exit 1
	@echo "  ✓ All tests executed"

help:
	@echo "=========================================="
	@echo "Available commands:"
	@echo "=========================================="
	@echo "  make            - Build link_sim"
	@echo "  make clean      - Remove object files only"
	@echo "  make mrproper   - Remove link_sim binary only"
	@echo "  make clean-pycache - Remove __pycache__ folders and .pyc files"
	@echo "  make clean-tests - Remove all test-generated files"
	@echo "  make clean-all  - Remove everything (objects, binary, tests, cache)"
	@echo "  make rebuild    - Rebuild link_sim from scratch"
	@echo "  make debug      - Build with debug symbols"
	@echo "  make test       - Compile and run all Python tests"
	@echo "=========================================="