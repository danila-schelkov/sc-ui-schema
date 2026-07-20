.PHONY: all
all: | clean build validate

.PHONY: validate
validate: validate.py
	python3 $<

.PHONY: build
build: build.py
	python3 $<

.PHONY: clean
clean:
	rm -rf build/
	rm -rf generated/