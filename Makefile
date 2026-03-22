# See gcc/clang manual to understand all flags
CFLAGS += -std=c99 # Define which version of the C standard to use
CFLAGS += -Wall # Enable the 'all' set of warnings
CFLAGS += -Werror # Treat all warnings as error
CFLAGS += -Wshadow # Warn when shadowing variables
CFLAGS += -Wextra # Enable additional warnings
CFLAGS += -O2 -D_FORTIFY_SOURCE=2 # Add canary code, i.e. detect buffer overflows
CFLAGS += -fstack-protector-all # Add canary code to detect stack smashing
CFLAGS += -D_XOPEN_SOURCE -D_POSIX_C_SOURCE=201112L # getopt, clock_getttime

SOURCES=$(wildcard *.c)
OBJECTS=$(SOURCES:.c=.o)

LDFLAGS= -rdynamic
ifneq ($(shell uname -s),Darwin) # Apple does not have clock_gettime
	LDFLAGS += -lrt              # hence does not need librealtime
endif

all: link_sim

debug: CFLAGS += -g -DDEBUG -Wno-unused-parameter -fno-omit-frame-pointer
debug: LDFLAGS += -lSegFault
debug: link_sim

link_sim: $(OBJECTS)

# Test files patterns (files created by tests)
TEST_FILES = test_*.txt test_*.bin output_*.txt output_*.bin \
             large_input.bin large_output.bin \
             test_input.txt test_output.txt \
             custom_output.txt missing_out.txt save_test.txt \
             custom_location.model 11m.model \
             test_perfect_*.bin test_latency_*.bin test_loss_*.bin \
             output_perfect_*.bin output_latency_*.bin output_loss_*.bin \
             performance_results.png \
             *.log *.out core *.core

# Python cache directories
PYCACHE_DIRS = __pycache__ tests/__pycache__ src/__pycache__
PYCACHE_FILES = *.pyc tests/*.pyc src/*.pyc

.PHONY: clean mrproper rebuild clean-tests clean-all clean-pycache help

clean:
	@rm -f $(OBJECTS)
	@echo "  ✓ Object files removed"

mrproper:
	@rm -f link_sim
	@echo "  ✓ Binary removed"

clean-pycache:
	@echo "Removing Python cache files..."
	@rm -rf $(PYCACHE_DIRS)
	@rm -f $(PYCACHE_FILES)
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "  ✓ Python cache removed"

clean-tests:
	@echo "Cleaning test files..."
	@rm -f $(TEST_FILES)
	@rm -f $(addprefix tests/, $(TEST_FILES))
	@rm -f $(addprefix src/, $(TEST_FILES))
	@rm -f $(addprefix ../, $(TEST_FILES))
	@echo "  ✓ Test files removed"

clean-all: clean clean-tests clean-pycache mrproper
	@echo "  ✓ All files cleaned"

rebuild: clean mrproper link_sim

# Help target to show available commands
help:
	@echo "=========================================="
	@echo "Available commands:"
	@echo "=========================================="
	@echo "  make            - Build link_sim"
	@echo "  make clean      - Remove object files only"
	@echo "  make mrproper   - Remove link_sim binary only"
	@echo "  make clean-pycache - Remove __pycache__ folders and .pyc files"
	@echo "  make clean-tests - Remove all test files (outputs, temp files, etc.)"
	@echo "  make clean-all  - Remove everything (objects, binary, tests, cache)"
	@echo "  make rebuild    - Rebuild link_sim from scratch"
	@echo "  make debug      - Build with debug symbols"
	@echo "=========================================="