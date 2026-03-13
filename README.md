# ToxicityObserver
ToxicityObserver is a bot that identifies likely toxic posts and reports them for moderation. Currently, it reads Steam discussions for the last day, uses Perspective API for scoring, and reports results to a Discord webhook. 

Currently in a very rough/initial state. One day it may become extensible to support other platforms and scoring methods or include other fanciness. That is not this day.

## Running it
Create your config file - copy `config.example.json` and fill in your values.

Then - 

`docker build -t toxicity-observer-dev .`

`docker run -v /path/to/config.dev.json:/app/config.json toxicity-observer-dev`

## Development Caching 
To avoid hammering Steam and/or Perspective API during development, you can enable caching for the Steam scraper and the Perspective scorer, both of which will cache results in the /cache/ directory. You can maintain this cache across runs with mounting the cache directory

```
docker run `
  -v "/path/to/config.dev.json:/app/config.json" `
  -v "/path/to/cache:/app/cache" `
  toxicity-observer-dev
```