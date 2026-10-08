-- Check for a running slide show, then send keys specifically to PowerPoint.
on run argv
    if (count of argv) is not 1 then error "Expected previous or next" number 64
    set direction to item 1 of argv
    if direction is not "previous" and direction is not "next" then error "Expected previous or next" number 64
    if application "Microsoft PowerPoint" is not running then error "PowerPoint is not running. Start a slide show first." number 1
    tell application "Microsoft PowerPoint"
        -- The window's 'active' property can return no result on macOS.
        -- Obtain the show through the active presentation instead.
        try
            set targetWindow to slide show window of active presentation
            set targetView to slideshow view of targetWindow
            get current show position of targetView
        on error
            error "No slide show is available for the active PowerPoint presentation. Start its slide show first." number 1
        end try
        -- PowerPoint's previous-slide AppleEvent can advance instead on macOS.
        activate
    end tell
    tell application "System Events"
        tell process "Microsoft PowerPoint"
            set frontmost to true
            if direction is "previous" then
                key code 123
            else
                key code 124
            end if
        end tell
    end tell
end run
