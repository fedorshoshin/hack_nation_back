(function () {
  var AgentSplit = {}
  var config = null
  var initialized = false
  var eventQueue = []

  // Metrics state
  var metrics = {
    clicks: 0,
    completed: false,
    startTime: null,
    friction_score: 0
  }

  function now() {
    return new Date().toISOString()
  }

  function safeString(value, maxLength) {
    if (value === null || value === undefined) {
      return null
    }

    return String(value).slice(0, maxLength || 300)
  }

  function log() {
    if (!config || !config.debug) {
      return
    }

    console.log.apply(console, ["[AgentSplit]"].concat(Array.prototype.slice.call(arguments)))
  }

  function getElementData(element) {
    if (!element) {
      return {}
    }

    return {
      tag: safeString(element.tagName, 50),
      id: safeString(element.id, 100),
      className: safeString(element.className, 200),
      text: safeString(element.innerText || element.textContent, 150),
      name: safeString(element.getAttribute("name"), 100),
      type: safeString(element.getAttribute("type"), 50),
      role: safeString(element.getAttribute("role"), 100),
      ariaLabel: safeString(element.getAttribute("aria-label"), 150),
      testId: safeString(element.getAttribute("data-testid"), 150)
    }
  }

  function getMetrics() {
    return {
      clicks: metrics.clicks,
      completed: metrics.completed,
      duration_ms: metrics.startTime ? Date.now() - metrics.startTime : 0,
      friction_score: metrics.friction_score
    }
  }

  function getBasePayload(eventName, payload) {
    return {
      campaign_id: config.campaignId,
      campaignId: config.campaignId,
      sessionId: config.sessionId,
      variant: config.variant,
      variant_name: config.variant_name,
      event: eventName,
      payload: payload || {},
      metrics: getMetrics(),
      success_event: config.successEvent || null,
      page: {
        href: window.location.href,
        origin: window.location.origin,
        pathname: window.location.pathname,
        search: window.location.search,
        title: document.title,
        referrer: document.referrer
      },
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight
      },
      userAgent: navigator.userAgent,
      timestamp: now()
    }
  }

  function sendToParent(eventData) {
    if (window.parent === window) {
      return
    }

    try {
      window.parent.postMessage(
        {
          source: "agentsplit",
          type: "event",
          data: eventData
        },
        "*"
      )
    } catch (error) {
      log("postMessage failed", error)
    }
  }

  function sendEvent(eventName, payload) {
    if (!config) {
      eventQueue.push({
        eventName: eventName,
        payload: payload
      })
      return
    }

    var eventData = getBasePayload(eventName, payload)

    log("track", eventData)

    sendToParent(eventData)

    if (!config.apiUrl) {
      return
    }

    fetch(config.apiUrl.replace(/\/$/, "") + "/events", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      keepalive: true,
      body: JSON.stringify(eventData)
    }).catch(function (error) {
      log("event send failed", error)
    })
  }

  function flushQueue() {
    var queue = eventQueue.slice()
    eventQueue = []

    queue.forEach(function (item) {
      sendEvent(item.eventName, item.payload)
    })
  }

  function trackPageView() {
    sendEvent("page_view", {
      path: window.location.pathname,
      search: window.location.search,
      title: document.title
    })
  }

  function listenClicks() {
    document.addEventListener(
      "click",
      function (event) {
        var target = event.target

        if (!(target instanceof HTMLElement)) {
          return
        }

        metrics.clicks++

        sendEvent("click", {
          element: getElementData(target),
          x: event.clientX,
          y: event.clientY
        })
      },
      true
    )
  }

  function listenFormSubmits() {
    document.addEventListener(
      "submit",
      function (event) {
        var target = event.target

        if (!(target instanceof HTMLFormElement)) {
          return
        }

        sendEvent("form_submit", {
          element: getElementData(target),
          action: safeString(target.action, 300),
          method: safeString(target.method, 20)
        })
      },
      true
    )
  }

  function listenErrors() {
    window.addEventListener("error", function (event) {
      metrics.friction_score++

      sendEvent("javascript_error", {
        message: safeString(event.message, 500),
        filename: safeString(event.filename, 300),
        line: event.lineno,
        column: event.colno
      })
    })

    window.addEventListener("unhandledrejection", function (event) {
      metrics.friction_score++

      sendEvent("unhandled_rejection", {
        reason: safeString(event.reason && (event.reason.stack || event.reason.message || event.reason), 800)
      })
    })
  }

  function patchHistoryMethod(methodName) {
    var original = history[methodName]

    history[methodName] = function () {
      var result = original.apply(this, arguments)

      setTimeout(function () {
        sendEvent("route_change", {
          method: methodName,
          path: window.location.pathname,
          search: window.location.search
        })
      }, 0)

      return result
    }
  }

  function listenRouteChanges() {
    patchHistoryMethod("pushState")
    patchHistoryMethod("replaceState")

    window.addEventListener("popstate", function () {
      sendEvent("route_change", {
        method: "popstate",
        path: window.location.pathname,
        search: window.location.search
      })
    })
  }

  AgentSplit.init = function (userConfig) {
    if (initialized) {
      log("already initialized")
      return
    }

    if (!userConfig) {
      throw new Error("AgentSplit.init config is required")
    }

    if (!userConfig.campaignId) {
      throw new Error("campaignId is required")
    }

    if (!userConfig.sessionId) {
      throw new Error("sessionId is required")
    }

    if (!userConfig.variant && !userConfig.variant_name) {
      throw new Error("variant_name is required")
    }

    config = {
      apiUrl: userConfig.apiUrl || "",
      campaignId: userConfig.campaignId,
      sessionId: userConfig.sessionId,
      variant: userConfig.variant || userConfig.variant_name,
      variant_name: userConfig.variant_name || userConfig.variant,
      successEvent: userConfig.successEvent || "",
      debug: Boolean(userConfig.debug)
    }

    metrics.startTime = Date.now()

    initialized = true

    listenClicks()
    listenFormSubmits()
    listenErrors()
    listenRouteChanges()
    trackPageView()
    flushQueue()

    log("initialized", config)
  }

  AgentSplit.track = function (eventName, payload) {
    if (!eventName) {
      throw new Error("eventName is required")
    }

    sendEvent(eventName, payload || {})
  }

  AgentSplit.completeTask = function (payload) {
    metrics.completed = true
    var data = payload || {}
    data.success_event = config ? config.successEvent : null
    if (config && config.variant_name) {
      data.variant_name = config.variant_name
    }
    sendEvent("task_completed", data)
  }

  AgentSplit.failTask = function (payload) {
    metrics.completed = false
    sendEvent("task_failed", payload || {})
  }

  AgentSplit.getMetrics = function () {
    return getMetrics()
  }

  AgentSplit.identify = function (payload) {
    sendEvent("identify", payload || {})
  }

  window.AgentSplit = AgentSplit
})()